from typing import Any, Optional
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.events.models import OktoberfestSheep, OktoberfestStatus


class OktoberfestEventService:
    """Solves the Oktoberfest beer-table seating puzzle using a backtracking solver."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.last_status: Optional[OktoberfestStatus] = None

    @staticmethod
    def have_common_interest(person1: dict[str, Any], person2: dict[str, Any]) -> bool:
        """Check if two persons share at least one common interest."""
        return bool(set(person1.get("interests", [])) & set(person2.get("interests", [])))

    @classmethod
    def is_valid(
        cls,
        seating: list[Optional[dict[str, Any]]],
        index: int,
        person: dict[str, Any],
    ) -> bool:
        """Check if placing a person at the given seat index satisfies all neighbour/opposite rules."""
        num_people = len(seating)
        half = num_people // 2

        # 1. Check neighbour on the bench (same side, so index != half)
        if index > 0 and index != half:
            prev_person = seating[index - 1]
            if prev_person and not cls.have_common_interest(prev_person, person):
                return False

        # 2. Check person sitting directly opposite
        if index >= half:
            opposite_person = seating[index - half]
            if opposite_person and not cls.have_common_interest(opposite_person, person):
                return False

        return True

    @classmethod
    def backtrack(
        cls,
        seating: list[Optional[dict[str, Any]]],
        people: list[dict[str, Any]],
        index: int,
    ) -> bool:
        """Recursively search for a valid seating order."""
        if index == len(people):
            return True

        for person in people:
            if not person.get("seated", False):
                seating[index] = person
                if cls.is_valid(seating, index, person):
                    person["seated"] = True
                    if cls.backtrack(seating, people, index + 1):
                        return True
                    person["seated"] = False

        seating[index] = None
        return False

    @classmethod
    def solve_seating(cls, sheeps_dict: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
        """Prepare sheep data and run the backtracking solver."""
        people: list[dict[str, Any]] = []
        for s_id, s_data in sheeps_dict.items():
            item = dict(s_data)
            item["sheep"] = str(s_id)
            item["seated"] = False
            people.append(item)

        seating: list[Optional[dict[str, Any]]] = [None] * len(people)
        if cls.backtrack(seating, people, 0):
            # Assert all seats are filled
            return [s for s in seating if s is not None]
        return None

    async def get_status(self) -> Optional[OktoberfestStatus]:
        """Fetch current status of the Oktoberfest event."""
        try:
            res = await self.client.api_call("farm", {"mode": "oktoberfest_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or "data" not in datablock:
                return None

            data_block = datablock.get("data", {})
            cooldown = int(data_block.get("cooldown_remain", 0))
            sheeps = data_block.get("sheeps", {})

            status = OktoberfestStatus(
                cooldown_remain=max(0, cooldown),
                sheeps_count=len(sheeps) if isinstance(sheeps, dict) else 0,
                is_ready=cooldown <= 0,
            )
            self.last_status = status
            return status

        except Exception as e:
            logger.warning(f"OktoberfestEventService: Fehler beim Abruf von oktoberfest_init: {e}")
            return None

    async def serve(self) -> bool:
        """Solve the seating arrangement and seat all sheep."""
        try:
            res = await self.client.api_call("farm", {"mode": "oktoberfest_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or "data" not in datablock:
                logger.debug("OktoberfestEventService: Kein aktives Oktoberfest-Event.")
                return False

            data_block = datablock.get("data", {})
            cooldown = data_block.get("cooldown_remain", 0)
            if cooldown is not None and int(cooldown) > 0:
                logger.debug(f"OktoberfestEventService: Cooldown läuft noch ({cooldown}s verbleibend).")
                return False

            sheeps = data_block.get("sheeps", {})
            if not isinstance(sheeps, dict) or not sheeps:
                logger.debug("OktoberfestEventService: Keine Schafe zum Platzieren vorhanden.")
                return False

            logger.info(
                f"OktoberfestEventService: Starte Backtracking-Solver für {len(sheeps)} Schafe am Biertisch..."
            )
            solution = self.solve_seating(sheeps)

            if not solution:
                logger.warning(
                    "OktoberfestEventService: Es konnte keine gültige Sitzordnung gefunden werden."
                )
                return False

            logger.info("OktoberfestEventService: Gültige Sitzordnung gefunden! Platziere Schafe...")
            for seat_idx, sheep in enumerate(solution, start=1):
                sheep_id = sheep["sheep"]
                logger.debug(f"Platz {seat_idx}: Platziere Schaf #{sheep_id} (Interessen: {sheep.get('interests')})")
                set_res = await self.client.api_call(
                    "farm",
                    {"mode": "oktoberfest_set_sheep", "slot": seat_idx, "sheep": sheep_id},
                )
                if not set_res.get("datablock", {}).get("data"):
                    logger.error(
                        f"OktoberfestEventService: Fehler beim Setzen von Schaf #{sheep_id} auf Platz {seat_idx}: {set_res}"
                    )
                    return False

            logger.info("OktoberfestEventService: Alle Schafe platziert. Schließe Runde ab (oktoberfest_finish)...")
            finish_res = await self.client.api_call("farm", {"mode": "oktoberfest_finish"})
            if finish_res.get("datablock", {}).get("data"):
                logger.info("OktoberfestEventService: Oktoberfest-Runde erfolgreich abgeschlossen und Belohnung erhalten!")
                await self.get_status()
                return True
            else:
                logger.warning(f"OktoberfestEventService: Fehler beim Abschließen der Runde: {finish_res}")
                return False

        except Exception as e:
            logger.error(f"OktoberfestEventService: Unerwarteter Fehler im Oktoberfest-Zyklus: {e}")
            return False
