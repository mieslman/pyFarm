import asyncio
import random
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from app.config import settings
from app.core.circuit_breaker import circuit_breaker
from app.core.client import MFFGameClient
from app.core.exceptions import CircuitBreakerOpenError
from app.modules.events import EventManager
from app.modules.farm_buildings.farm_service import FarmService
from app.modules.farmersmarket.service import FarmersMarketService
from app.modules.foodworld.service import FoodworldService
from app.modules.forestry.forestry_service import ForestryService
from app.modules.formula import FormulaDealerService
from app.modules.helpers.helpers_service import HelpersService
from app.modules.insecthotel import InsectHotelService
from app.modules.seasonpass import SeasonPassConfig, SeasonPassService
from app.modules.stall import FruitStallService
from app.services.quest_service import QuestService
from app.services.stock_service import StockService
from app.services.trade_service import TradeService


class WorkerScheduler:
    """Orchestrates the periodic single-account automation loop (10-minute cycle).

    Lifecycle per cycle:
    1. Login to upstream game server
    2. Initialize / update StockService and Catalog
    3. Determine active Quests & missing crops
    4. Execute FarmService.loop() (Fields, Animal Sheds, Fuelstation)
    5. Execute ForestryService.serve() (Forest, Sawmill, Carpentry, Farmis)
    6. Execute HelpersService.serve() (Braver Ben, Waltraud, Lottery, Windmill)
    7. Execute TradeService.serve() (Sell surplus crops on marketplace)
    8. Logout cleanly from game server
    """

    def __init__(self):
        self.is_running: bool = False
        self.current_state: str = "IDLE"  # IDLE, RUNNING, SLEEPING, ERROR
        self.last_run_time: datetime | None = None
        self.next_run_time: float | None = None
        self.last_error: str | None = None
        self.cycle_lock: asyncio.Lock = asyncio.Lock()
        self._background_task: asyncio.Task | None = None
        self.last_stock_service: StockService | None = None
        self.last_farm_service: FarmService | None = None
        self.last_forestry_service: ForestryService | None = None
        self.last_farmersmarket_service: FarmersMarketService | None = None
        self.last_foodworld_service: FoodworldService | None = None
        self.last_sushibar_service: Any | None = None
        self.last_spicehouse_service: Any | None = None
        self.last_seasonpass_service: SeasonPassService | None = None
        self.last_event_manager: EventManager | None = None
        self.last_insecthotel_service: InsectHotelService | None = None
        self.last_stall_service: FruitStallService | None = None
        self.last_vehicle_service: Any | None = None
        self.last_quest_requirements: dict[int, int] = {}
        self.last_cycle_results: dict[str, Any] = {}

    async def broadcast_status(self):
        """Broadcast current scheduler state and countdown over WebSocket."""
        try:
            from app.api.websocket import ws_manager

            next_sec = None
            if self.next_run_time:
                now_ts = datetime.now(UTC).timestamp()
                next_sec = max(0, int(self.next_run_time - now_ts))
            await ws_manager.broadcast(
                {
                    "type": "status",
                    "data": {
                        "state": self.current_state,
                        "is_running": self.is_running,
                        "next_run_seconds": next_sec,
                        "last_error": self.last_error,
                        "circuit_breaker": circuit_breaker.get_status(),
                    },
                }
            )
        except Exception:  # noqa: BLE001, S110
            pass

    async def run_cycle(self) -> dict[str, Any]:
        """Execute a single full bot automation cycle."""
        if self.cycle_lock.locked():
            logger.info("WorkerScheduler: Zyklus läuft bereits, Anfrage übersprungen.")
            return {"status": "skipped", "reason": "already_running"}

        if circuit_breaker.is_open:
            logger.warning(
                f"WorkerScheduler: Zyklus übersprungen, da Circuit-Breaker AKTIV ist: "
                f"{circuit_breaker.trip_reason}"
            )
            self.current_state = "PAUSED_CIRCUIT_BREAKER"
            self.last_error = f"Circuit-Breaker aktiv: {circuit_breaker.trip_reason}"
            await self.broadcast_status()
            return {
                "status": "skipped",
                "reason": "circuit_breaker_open",
                "trip_reason": circuit_breaker.trip_reason,
            }

        async with self.cycle_lock:
            self.current_state = "RUNNING"
            self.last_run_time = datetime.now(UTC)
            self.last_error = None
            await self.broadcast_status()

            account = settings.account
            if (
                not account.username
                or not account.password
                or account.username in ("", "DeinBenutzername")
            ):
                logger.warning("WorkerScheduler: Keine Zugangsdaten in .env konfiguriert.")
                self.current_state = "IDLE"
                await self.broadcast_status()
                return {"status": "error", "reason": "no_credentials"}

            client = MFFGameClient(
                server=account.server,
                username=account.username,
                password=account.password,
            )

            results: dict[str, Any] = {}
            farm_service = None
            forestry_service = None
            stock_service = None
            try:
                # 1. Login
                logger.info(
                    f"========== WorkerScheduler: Starte Zyklus für '{account.username}' auf Server {account.server} =========="
                )
                await client.login()

                # Anti-detection pause
                await asyncio.sleep(random.uniform(1.0, 2.0))

                # 2. Init stock service & catalog
                main_page = await client.client.get(
                    f"https://s{client.server}.myfreefarm.de/main.php"
                )
                stock_service = StockService(client)
                await stock_service.init(main_page.text)
                self.last_stock_service = stock_service
                results["stock_initialized"] = True

                # 3. Quest analysis
                quest_requirements: dict[int, int] = {}
                if settings.quest.enabled:
                    try:
                        quest_service = QuestService(client, stock_service)
                        await quest_service.serve()
                        quest_requirements = quest_service.get_requirements_dict()
                        self.last_quest_requirements = quest_requirements
                        results["quest_requirements"] = quest_requirements
                        if quest_requirements:
                            logger.info(
                                f"WorkerScheduler: Quest-Bedarfe für Ackerbau: {quest_requirements}"
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler bei Quest-Prüfung: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 4. Farm buildings: Äcker, Ställe, Biosprit-Anlage, Sushi-Bar
                if (
                    settings.agriculture.enabled
                    or settings.fuelstation.enabled
                    or settings.sushibar.enabled
                    or settings.spicehouse.enabled
                ):
                    farm_service = FarmService(client)
                    quest_status_main = (
                        quest_service.queststatus.get("main", {})
                        if quest_service and quest_service.queststatus
                        else None
                    )
                    catalog_dict = stock_service.products if stock_service else None
                    await farm_service.loop(
                        stock_service=stock_service,
                        quest_requirements=quest_requirements,
                        quest_status_main=quest_status_main,
                        catalog=catalog_dict,
                    )
                    self.last_farm_service = farm_service
                    if farm_service.sushibars:
                        self.last_sushibar_service = farm_service.sushibars[0]
                    if farm_service.spicehouses:
                        self.last_spicehouse_service = farm_service.spicehouses[0]
                    self.last_vehicle_service = farm_service.vehicle_service
                    results["farm_service"] = {
                        "fields": len(farm_service.fields),
                        "sheds": len(farm_service.sheds),
                        "fuelstations": len(farm_service.fuelstations),
                        "sushibars": len(farm_service.sushibars),
                        "spicehouses": len(farm_service.spicehouses),
                        "vehicles": len(farm_service.vehicle_service.vehicles),
                    }

                await asyncio.sleep(random.uniform(0.8, 1.8))

                # 5. Forestry (Baumerei)
                if settings.forestry.enabled:
                    try:
                        forestry_service = ForestryService(client)
                        await forestry_service.serve()
                        self.last_forestry_service = forestry_service
                        results["forestry"] = True
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler in Forstwirtschaft: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.5))

                # 6. Daily helpers & bonuses
                if settings.helpers.enabled:
                    try:
                        helpers_service = HelpersService(client, stock_service)
                        helpers_res = await helpers_service.serve()
                        results["helpers"] = helpers_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler bei täglichen Helfern: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 6b. Formula Dealer & Powerups (Stadt 2)
                if settings.formula_dealer.enabled:
                    try:
                        fd_service = FormulaDealerService(
                            client=client,
                            stock_service=stock_service,
                            config=settings.formula_dealer,
                        )
                        fd_res = await fd_service.run_cycle()
                        results["formula_dealer"] = fd_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler beim FormulaDealer: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 7. Farmers Market (Bauernmarkt Dorf 2)
                if settings.farmersmarket.enabled:
                    try:
                        fm_service = FarmersMarketService(
                            client, stock_service, settings.farmersmarket
                        )
                        fm_res = await fm_service.run_cycle()
                        self.last_farmersmarket_service = fm_service
                        results["farmersmarket"] = fm_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler im Bauernmarkt: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 8. Foodworld (Picknick-Bereich & Restaurant)
                if settings.foodworld.enabled:
                    try:
                        fw_service = FoodworldService(
                            client, stock_service, config=settings.foodworld
                        )
                        fw_res = await fw_service.run_cycle()
                        self.last_foodworld_service = fw_service
                        results["foodworld"] = fw_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler in Foodworld: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 9. Seasonpass (Saisonale Reise)
                if settings.seasonpass.enabled:
                    try:
                        sp_service = SeasonPassService(
                            client=client,
                            config=SeasonPassConfig(
                                enabled=settings.seasonpass.enabled,
                                auto_claim_rewards=settings.seasonpass.auto_claim_rewards,
                                preferred_field_farm=settings.seasonpass.preferred_field_farm,
                                preferred_field_pos=settings.seasonpass.preferred_field_pos,
                                preferred_forestry_pos=settings.seasonpass.preferred_forestry_pos,
                            ),
                        )
                        sp_res = await sp_service.serve(
                            stock_service=stock_service,
                            farm_service=self.last_farm_service,
                        )
                        self.last_seasonpass_service = sp_service
                        results["seasonpass"] = sp_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler beim Seasonpass: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 10. Saisonevents (Bereich A: Kalender, Liefertouren, Event-Garten, Oktoberfest etc.)
                if settings.events.enabled:
                    try:
                        event_manager = EventManager(client=client)
                        ev_res = await event_manager.serve()
                        self.last_event_manager = event_manager
                        results["events"] = ev_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler bei Saisonevents: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 11. Insektenhotel (Futterlager & Kasse)
                if settings.insecthotel.enabled:
                    try:
                        ih_service = InsectHotelService(client=client)
                        ih_res = await ih_service.serve(stock_service=stock_service)
                        self.last_insecthotel_service = ih_service
                        results["insecthotel"] = ih_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler beim Insektenhotel: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 12. Obststand / Marktbude (Früchte-Slots & Belohnungen)
                if settings.stall.enabled:
                    try:
                        stall_service = FruitStallService(client=client)
                        stall_res = await stall_service.serve(stock_service=stock_service)
                        self.last_stall_service = stall_service
                        results["stall"] = stall_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler beim Obststand: {e}")

                await asyncio.sleep(random.uniform(0.5, 1.2))

                # 13. Trade (Überschussverkauf)
                if settings.trade.enabled:
                    try:
                        trade_service = TradeService(client, stock_service)
                        trade_res = await trade_service.serve()
                        results["trade"] = trade_res
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WorkerScheduler: Fehler beim Handel: {e}")

                self.last_cycle_results = results
                logger.info(
                    "========== WorkerScheduler: Zyklus erfolgreich abgeschlossen =========="
                )

            except CircuitBreakerOpenError as e:
                logger.error(f"WorkerScheduler: Zyklus wegen Circuit-Breaker Not-Aus sofort abgebrochen: {e}")
                self.current_state = "PAUSED_CIRCUIT_BREAKER"
                self.last_error = str(e)
                results["error"] = str(e)
                results["circuit_breaker"] = circuit_breaker.get_status()
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(f"WorkerScheduler: Fehler während des Zyklus: {e}")
                self.last_error = str(e)
                results["error"] = str(e)
            finally:
                # 8. Clean logout
                try:
                    await client.logout()
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"WorkerScheduler: Logout-Hinweis: {e}")

                self.current_state = "SLEEPING" if self.is_running else "IDLE"
                await self.broadcast_status()

            return results

    async def start(self):
        """Start the recurring scheduler loop."""
        self.is_running = True
        logger.info("WorkerScheduler: Hintergrund-Dienst gestartet.")

        # Immediate initial cycle on startup
        try:
            await self.run_cycle()
        except Exception as e:  # noqa: BLE001
            logger.error(f"WorkerScheduler: Fehler im Initial-Zyklus: {e}")

        while self.is_running:
            interval = settings.poll_interval_seconds
            # Add anti-detection jitter (+/- 5%)
            jitter = random.uniform(-0.05, 0.05) * interval
            sleep_duration = max(60, int(interval + jitter))

            self.next_run_time = datetime.now(UTC).timestamp() + sleep_duration
            logger.info(
                f"WorkerScheduler: Nächster Zyklus in {sleep_duration} Sekunden (~{sleep_duration // 60} Min)..."
            )

            # Sleep in 1s slices to react immediately to stop() signals
            for sec_idx in range(sleep_duration):
                if not self.is_running:
                    break
                if sec_idx % 2 == 0:
                    await self.broadcast_status()
                await asyncio.sleep(1)

            if not self.is_running:
                break

            try:
                await self.run_cycle()
            except Exception as e:  # noqa: BLE001
                logger.error(f"WorkerScheduler: Unerwarteter Fehler in der Schleife: {e}")

        self.current_state = "IDLE"
        await self.broadcast_status()
        logger.info("WorkerScheduler: Hintergrund-Dienst beendet.")

    def stop(self):
        """Signal the scheduler to stop gracefully."""
        self.is_running = False
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
        self.current_state = "IDLE"


# Global singleton worker instance
worker_scheduler = WorkerScheduler()
