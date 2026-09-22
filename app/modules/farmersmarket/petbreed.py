"""PetBreed (Tierzucht) service for breeding pets and crafting tools in Dorf 2.

NOTE: This module is inactive by default (pet_breed_enabled = False) per user configuration.
"""

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.farmersmarket.models import (
    PetBreedQuest,
    PetBreedSlot,
    PetBreedState,
    PetBreedTool,
)
from app.services.stock_service import StockService


class PetBreedService:
    """Automates pet breeding and tool crafting in the Dorf 2 breeding station.

    Guarded by the enabled flag: if False, no upstream calls are made.
    """

    def __init__(
        self,
        client: MFFGameClient,
        stock_service: StockService | None = None,
        enabled: bool = False,
        daily_parts_enabled: bool = True,
    ) -> None:
        self.client = client
        self.stock_service = stock_service
        self.enabled = enabled
        self.daily_parts_enabled = daily_parts_enabled
        self.state = PetBreedState()

    def update(self, farmersmarket_data: dict) -> None:
        """Parse pet breeding data if present."""
        pets_raw = farmersmarket_data.get("pets", {})
        if not isinstance(pets_raw, dict):
            return

        self.state.daily = int(pets_raw.get("daily", 0) or 0)

        # 1. Parse slots
        prod_dict = pets_raw.get("production", {})
        parsed_slots: dict[int, PetBreedSlot] = {}
        if isinstance(prod_dict, dict):
            for s_str, s_data in prod_dict.items():
                try:
                    s_id = int(s_str)
                    tool_pid = None
                    if "1" in s_data and isinstance(s_data["1"], dict):
                        tool_pid = int(s_data["1"].get("pid", 0) or 0)
                    slot = PetBreedSlot(
                        slot_id=s_id,
                        duration=int(s_data.get("duration", 0) or 0),
                        gone=int(s_data.get("gone", 0) or 0),
                        tool_id=tool_pid,
                        block=int(s_data.get("block", 0) or 0),
                        coins=int(s_data.get("coins", 0) or 0),
                    )
                    parsed_slots[s_id] = slot
                except (ValueError, TypeError):
                    pass
        self.state.slots = parsed_slots

        # 2. Parse quest
        q_data = pets_raw.get("quest", {})
        if isinstance(q_data, dict):
            products: dict[int, int] = {}
            for p_str, amt in q_data.get("products", {}).items():
                try:
                    products[int(p_str)] = int(amt)
                except (ValueError, TypeError):
                    pass
            self.state.quest = PetBreedQuest(
                quest_id=str(q_data.get("questid", "")),
                remain=int(q_data.get("remain", 0) or 0),
                products=products,
            )

        # 3. Parse tools
        config_tools = pets_raw.get("config", {}).get("tools", {})
        parsed_tools: dict[int, PetBreedTool] = {}
        if isinstance(config_tools, dict):
            for t_str, t_data in config_tools.items():
                try:
                    t_id = int(t_str)
                    needs: dict[int, int] = {}
                    for n_str, n_amt in t_data.get("needs", {}).items():
                        needs[int(n_str)] = int(n_amt)
                    parsed_tools[t_id] = PetBreedTool(
                        tool_id=t_id,
                        type=str(t_data.get("type", "")),
                        needs=needs,
                    )
                except (ValueError, TypeError):
                    pass
        self.state.tools = parsed_tools

    async def buy_daily_parts(self) -> bool:
        """Buy and open daily free pet parts package if available."""
        if not self.daily_parts_enabled:
            return False

        if self.state.daily <= 0:
            return False

        logger.info("Tierzucht (PetBreed): Hole tägliches Bauteile-Päckchen ab...")
        try:
            await self.client.api_call("farm", {"mode": "pets_buy_parts", "id": 1, "amount": 1})
            open_res = await self.client.api_call("farm", {"mode": "pets_open_pack", "type": 1})
            self.state.daily = 0
            logger.info("Tierzucht (PetBreed): Tägliches Bauteile-Päckchen erfolgreich abgeholt und geöffnet.")
            if self.stock_service and "updateblock" in open_res:
                self.stock_service.update(open_res)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Tierzucht: Fehler beim Abholen des Bauteile-Päckchens: {e}")
            return False

    async def run_cycle(self) -> int:
        """Run pet breeding cycle if enabled in configuration."""
        if self.daily_parts_enabled:
            await self.buy_daily_parts()

        if not self.enabled:
            logger.debug("Tierzucht (PetBreed): Laut Konfiguration inaktiv - Zucht-Zyklus wird übersprungen.")
            return 0

        logger.info("Tierzucht (PetBreed): Starte Zucht-Zyklus...")
        harvested = await self._harvest()
        return harvested

    async def _harvest(self) -> int:
        """Harvest finished tools/breeding items."""
        harvested = 0
        for slot in self.state.slots.values():
            if slot.is_ready:
                logger.info(f"Tierzucht: Ernte fertiges Zuchtergebnis aus Slot #{slot.slot_id}...")
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "pets_harvest_production",
                            "slot": slot.slot_id,
                            "position": 1,
                        },
                    )
                    harvested += 1
                    slot.tool_id = None
                    slot.gone = 0
                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Tierzucht: Fehler beim Ernten von Slot #{slot.slot_id}: {e}")
        return harvested
