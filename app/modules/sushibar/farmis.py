from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.sushibar.models import SushiFarmi


class SushiFarmiService:
    """Manages seated Farmi customers at the Sushi-Bar tables (Slots 1..10).

    Collects finished Farmis once all dishes in all requested categories have been eaten.
    Strictly forbids 'sushibar_finisheat' to prevent accidental Coin spending.
    """

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.farmis: list[SushiFarmi] = []

    def update_farmis(
        self,
        raw_farmis: dict[str, Any] | None,
        catalog: dict[int, Any] | None = None,
    ):
        """Parse raw farmis from sushibar response."""
        self.farmis = []
        if not isinstance(raw_farmis, dict):
            return

        for slot_str, data in raw_farmis.items():
            if not str(slot_str).isdigit() or not isinstance(data, dict):
                continue
            slot_nr = int(slot_str)
            farmi_id = str(data.get("id", ""))
            if not farmi_id:
                continue

            img = str(data.get("img", ""))
            inner_data = data.get("data", {})
            need = inner_data.get("need", {}) if isinstance(inner_data, dict) else {}
            have = inner_data.get("have", {}) if isinstance(inner_data, dict) else {}

            eat = data.get("eat", {})
            eat_pid = None
            eat_product_name = ""
            eat_duration = 0
            eat_remain = 0

            if isinstance(eat, dict) and eat.get("pid"):
                eat_pid = int(eat["pid"])
                eat_duration = int(eat.get("duration", 0))
                eat_remain = int(eat.get("remain", 0))
                if catalog and eat_pid in catalog:
                    eat_product_name = catalog[eat_pid].name

            finishdate = str(data.get("finishdate", "0"))
            createdate = str(data.get("createdate", "0"))

            self.farmis.append(
                SushiFarmi(
                    id=farmi_id,
                    slot=slot_nr,
                    img=img,
                    need=need,
                    have=have,
                    eat_pid=eat_pid,
                    eat_product_name=eat_product_name,
                    eat_duration=eat_duration,
                    eat_remain=eat_remain,
                    finishdate=finishdate,
                    createdate=createdate,
                )
            )

        self.farmis.sort(key=lambda f: f.slot)

    async def collect(
        self,
        auto_farmi: bool = True,
        update_callback: Any | None = None,
    ) -> int:
        """Cash out all satisfied Farmis whose meal is complete."""
        if not auto_farmi:
            return 0

        collected_count = 0
        for farmi in self.farmis:
            if farmi.is_ready_to_cash:
                logger.info(
                    f"Sushi-Bar: Kassiere zufriedenen Gast an Platz {farmi.slot} (Farmi ID {farmi.id}) ab..."
                )
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "sushibar_finishfarmi",
                            "slot": farmi.slot,
                        },
                    )
                    collected_count += 1
                    if update_callback and isinstance(res, dict):
                        await update_callback(res)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Sushi-Bar: Fehler beim Kassieren von Gast {farmi.slot}: {e}")

        return collected_count
