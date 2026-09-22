from typing import Any

from loguru import logger

from app.config import QuestConfig, settings
from app.core.client import MFFGameClient
from app.core.exceptions import UpstreamAPIError
from app.models.quest import QuestRequirement, QuestStatus
from app.services.stock_service import StockService


class QuestService:
    """Tracks active game quests and analyzes missing crops/products for planting strategies."""

    def __init__(
        self,
        client: MFFGameClient,
        stock_service: StockService,
        config: QuestConfig | None = None,
    ):
        self.client = client
        self.stock_service = stock_service
        self.config = config or settings.quest
        self.current_quest: QuestStatus | None = None
        self.queststatus: dict[str, Any] = {}
        self.main_quests: dict[str, Any] = {}

    def parse_quest_data(self, quest_nr: int, quest_data: dict[str, Any]) -> QuestStatus:
        """Parse raw quest JSON into QuestStatus model and compare against stock."""
        requirements: list[QuestRequirement] = []
        raw_data = quest_data.get("data", {})

        # Extract title
        title = quest_data.get("title")
        if not title:
            if isinstance(raw_data, dict) and "6" in raw_data:
                title = str(raw_data["6"])
            elif isinstance(raw_data, (list, tuple)) and len(raw_data) > 6:
                title = str(raw_data[6])
            else:
                title = f"Quest #{quest_nr}"

        # Upstream structure: requirements are strictly inside step 1: data[1][0] or data["1"][0]
        # Never parse top-level keys of raw_data as product IDs (as key 8 is a timer, key 0 is metadata, etc.)
        raw_requirements: dict[str, Any] = {}
        step_1 = None

        if isinstance(raw_data, dict):
            if "1" in raw_data or 1 in raw_data:
                step_1 = raw_data.get("1") or raw_data.get(1)
            elif "products" in raw_data:
                # e.g. infinite quests
                raw_requirements = raw_data["products"]
        elif isinstance(raw_data, (list, tuple)) and len(raw_data) > 1:
            step_1 = raw_data[1]

        if isinstance(step_1, (list, tuple)) and len(step_1) > 0 and isinstance(step_1[0], dict):
            raw_requirements = step_1[0]
        elif isinstance(step_1, dict):
            raw_requirements = step_1

        for pid_str, needed_amt in raw_requirements.items():
            if str(pid_str).isdigit():
                pid = int(pid_str)
                try:
                    amount_needed = int(needed_amt)
                except (ValueError, TypeError):
                    continue

                prod = self.stock_service.get_product(pid)
                name = prod.name if prod else f"Produkt {pid}"
                curr_stock = prod.total_amount if prod else 0
                missing = max(0, amount_needed - curr_stock)

                requirements.append(
                    QuestRequirement(
                        pid=pid,
                        name=name,
                        amount_needed=amount_needed,
                        current_stock=curr_stock,
                        missing=missing,
                    )
                )

        is_ready = len(requirements) > 0 and all(r.missing == 0 for r in requirements)

        return QuestStatus(
            quest_nr=quest_nr,
            title=title,
            requirements=requirements,
            is_ready=is_ready,
        )

    async def fetch_quest_status(self, quest_nr: int | None = None) -> QuestStatus | None:
        """Fetch current quest state from game server and parse active requirements."""
        target_nr = quest_nr or self.config.quest_nr

        try:
            res = await self.client.api_call("quest", {"action": "init", "campaign": 1, "farm": 1})
        except UpstreamAPIError as e:
            logger.warning(f"QuestService: Konnte Quest-Status nicht abfragen: {e}")
            return None

        # Update in-memory stock if server returned updateblock
        self.stock_service.update(res)

        quest_status_block = res.get("updateblock", {}).get("queststatus", {})
        main_quests = quest_status_block.get("main", {})
        self.queststatus = quest_status_block
        self.main_quests = main_quests

        if not main_quests or not isinstance(main_quests, dict):
            logger.info("QuestService: Keine Hauptquests in queststatus.main vorhanden.")
            return None

        self.active_quests = {}
        for k, v in main_quests.items():
            if isinstance(v, dict) and v.get("remain", 0) >= 0:
                c_id = int(k) if str(k).isdigit() else 1
                q_id = int(v.get("questid", c_id)) if str(v.get("questid", "")).isdigit() else c_id
                parsed = self.parse_quest_data(q_id, v)
                self.active_quests[c_id] = parsed

        if target_nr is not None and target_nr in self.active_quests:
            self.current_quest = self.active_quests[target_nr]
        elif self.active_quests:
            self.current_quest = self.active_quests.get(1) or next(iter(self.active_quests.values()))
        else:
            self.current_quest = None

        if not self.current_quest:
            logger.info("QuestService: Keine aktuell aktive (nicht abgelaufene) Quest vorhanden.")
            return None

        campaign_summaries = [
            f"Kampagne {cid}: Q#{q.quest_nr} ('{q.title}')"
            for cid, q in self.active_quests.items()
        ]
        logger.info(
            f"QuestService: {len(self.active_quests)} aktive Kampagne(n) geladen: "
            f"{', '.join(campaign_summaries)}."
        )
        return self.current_quest

    def get_requirements_dict(self, campaign_id: int | None = None) -> dict[int, int]:
        """Return dict of {pid: amount_needed} across active campaigns or for a specific campaign."""
        if campaign_id is not None:
            q = self.active_quests.get(campaign_id)
            if not q:
                return {}
            return {req.pid: req.amount_needed for req in q.requirements}

        combined: dict[int, int] = {}
        for q in self.active_quests.values():
            for req in q.requirements:
                combined[req.pid] = max(combined.get(req.pid, 0), req.amount_needed)
        if not combined and self.current_quest:
            return {req.pid: req.amount_needed for req in self.current_quest.requirements}
        return combined

    async def buy_missing(self) -> int:
        """Attempt to buy missing quest requirements if auto_buy_missing is True."""
        if not self.config.auto_buy_missing or not self.current_quest:
            return 0

        missing_reqs = [
            {"pid": req.pid, "amount": req.missing}
            for req in self.current_quest.requirements
            if req.missing > 0
        ]

        if not missing_reqs:
            return 0

        logger.info(
            f"QuestService: Versuche {len(missing_reqs)} fehlende Quest-Produkte zuzukaufen..."
        )
        success = await self.stock_service.grasp_products(missing_reqs)
        if success:
            logger.info("QuestService: Fehlende Quest-Produkte erfolgreich beschafft.")
            # Refresh quest status calculations with updated stock
            if self.current_quest:
                for req in self.current_quest.requirements:
                    prod = self.stock_service.get_product(req.pid)
                    if prod:
                        req.current_stock = prod.total_amount
                        req.missing = max(0, req.amount_needed - req.current_stock)
                self.current_quest.is_ready = all(
                    r.missing == 0 for r in self.current_quest.requirements
                )
        return len(missing_reqs)

    async def serve(self) -> QuestStatus | None:
        """Run QuestService automation cycle."""
        if not self.config.enabled:
            return None

        logger.info("---------- QuestService: Starte Zyklus ----------")
        status = await self.fetch_quest_status()
        if status and self.config.auto_buy_missing:
            await self.buy_missing()
        return status
