from typing import Any, Optional
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.events.models import DeliverySpot, DeliveryStatus


class DeliveryEventService:
    """Manages seasonal delivery events and automated tour optimization."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.last_status: Optional[DeliveryStatus] = None

    async def get_status(self) -> Optional[DeliveryStatus]:
        """Fetch and parse current delivery event state."""
        try:
            res = await self.client.api_call("farm", {"mode": "deliveryevent_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or "data" not in datablock:
                return None

            data_block = datablock.get("data", {})
            actual_points = int(data_block.get("points", 0))

            current_tour = data_block.get("tour", {})
            tour_remain = int(current_tour.get("remain", -1)) if isinstance(current_tour, dict) else -1
            tour_spot = str(current_tour.get("spot", "")) if isinstance(current_tour, dict) else ""

            status = DeliveryStatus(
                points=actual_points,
                active_tour_remain=max(0, tour_remain),
                active_spot=tour_spot,
            )
            self.last_status = status
            return status

        except Exception as e:
            logger.warning(f"DeliveryEventService: Fehler beim Abruf von deliveryevent_init: {e}")
            return None

    async def serve(self) -> bool:
        """Evaluate tours and launch the most point-efficient delivery tour."""
        try:
            res = await self.client.api_call("farm", {"mode": "deliveryevent_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or "data" not in datablock:
                logger.debug("DeliveryEventService: Kein aktives Delivery-Event.")
                return False

            data_block = datablock.get("data", {})
            config_spots = datablock.get("config", {}).get("spots", {})
            if not config_spots:
                logger.debug("DeliveryEventService: Keine Spots in Event-Konfiguration.")
                return False

            actual_points = int(data_block.get("points", 0))

            # 1. Check if a tour is currently active
            current_tour = data_block.get("tour", {})
            if isinstance(current_tour, dict):
                remain = current_tour.get("remain", -1)
                if remain is not None and remain >= 0:
                    logger.debug(
                        f"DeliveryEventService: Tour zu Spot {current_tour.get('spot')} läuft noch ({remain}s verbleibend)."
                    )
                    return False

            # 2. Compute efficiency outcome (points per duration second)
            spots: list[DeliverySpot] = []
            for spot_id, spot_info in config_spots.items():
                pts = int(spot_info.get("points", 0))
                dur = int(spot_info.get("duration", 1)) or 1
                name = str(spot_info.get("name", f"Spot {spot_id}"))
                outcome = pts / float(dur)
                spots.append(
                    DeliverySpot(
                        spot_id=str(spot_id),
                        name=name,
                        points=pts,
                        duration=dur,
                        outcome=outcome,
                    )
                )

            # Sort descending by outcome (points/s)
            spots.sort(key=lambda s: s.outcome, reverse=True)

            # Keep top candidates and prioritize higher points
            top_candidates = spots[:2]
            top_candidates.sort(key=lambda s: s.points, reverse=True)

            # 3. Find the best affordable tour
            chosen_spot: Optional[DeliverySpot] = None
            for spot in top_candidates:
                if spot.points <= actual_points:
                    chosen_spot = spot
                    break

            # Fallback across all spots if top 2 were too expensive
            if not chosen_spot:
                affordable = [s for s in spots if s.points <= actual_points]
                if affordable:
                    affordable.sort(key=lambda s: s.points, reverse=True)
                    chosen_spot = affordable[0]

            if not chosen_spot:
                logger.debug(
                    f"DeliveryEventService: Nicht genügend Event-Punkte ({actual_points}) für verfügbare Touren."
                )
                return False

            # 4. Start the tour
            logger.info(
                f"DeliveryEventService: Starte Tour zu '{chosen_spot.name}' (Spot #{chosen_spot.spot_id}) "
                f"für {chosen_spot.points} Punkte (Dauer: {chosen_spot.duration}s, Effizienz: {chosen_spot.outcome:.4f} Pkt/s)..."
            )
            start_res = await self.client.api_call(
                "farm",
                {"mode": "deliveryevent_starttour", "spot": chosen_spot.spot_id},
            )

            if "datablock" in start_res:
                logger.info(f"DeliveryEventService: Tour #{chosen_spot.spot_id} erfolgreich gestartet.")
                await self.get_status()
                return True
            else:
                logger.warning(f"DeliveryEventService: Fehler beim Starten der Tour: {start_res}")
                return False

        except Exception as e:
            logger.error(f"DeliveryEventService: Unerwarteter Fehler im Delivery-Zyklus: {e}")
            return False
