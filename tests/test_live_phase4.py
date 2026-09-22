import pytest

from app.config import settings
from app.core.client import MFFGameClient
from app.modules.farm_buildings.fuelstation import Fuelstation
from app.modules.forestry.forestry_service import ForestryService
from app.services.quest_service import QuestService
from app.services.stock_service import StockService


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_phase4_inspection():
    """Live Read-Only Inspection Test for Phase 4 against MyFreeFarm Server.

    Inspects:
    1. Forestry (Baumerei): Tree slots, Sawmill, Carpentry, Wood stock, Farmis.
    2. Active Quests: Current quest nr, required crops, current inventory, deficit.
    3. Daily Helpers & Bonuses: Braver Ben, Goldesel, Losbude, Windmühle.

    Run with: uv run pytest tests/test_live_phase4.py -s
    """
    account = settings.account
    if not account.username or not account.password or account.username in ("", "DeinBenutzername"):
        pytest.skip("Keine Live-Zugangsdaten in .env hinterlegt.")

    client = MFFGameClient(
        server=account.server,
        username=account.username,
        password=account.password,
    )

    try:
        # 1. Login
        await client.login()
        assert client.rid is not None

        # 2. Init stock service for catalog
        main_page = await client.client.get(f"https://s{client.server}.myfreefarm.de/main.php")
        stock_service = StockService(client)
        await stock_service.init(main_page.text)

        print(f"\n{'=' * 75}")
        print(
            f"MyFreeFarm Phase 4 Live-Inspektion (Server {client.server}, User '{client.username}')"
        )
        print(f"{'=' * 75}")

        # 3. Forestry Inspection
        forestry_svc = ForestryService(client)
        await forestry_svc.update()

        print("\n--- 1. Forstwirtschaft (Baumerei) ---")
        trees = forestry_svc.forrest.trees
        occupied_trees = [t for t in trees if not t.is_empty]
        ready_trees = [t for t in trees if t.is_ready_to_cut]
        unwatered_trees = [t for t in trees if t.needs_water]

        print(
            f"Baumplaetze: {len(occupied_trees)}/25 bepflanzt | "
            f"Schlagreif: {len(ready_trees)} | Nicht gegoessen: {len(unwatered_trees)}"
        )

        print(f"Sägewerk Slots: {len(forestry_svc.sawmill.slots)}")
        for s_id, s in forestry_svc.sawmill.slots.items():
            print(
                f"  - Slot {s_id}: Busy={s.busy}, PID={s.productid}, Remain={s.remain}s, Ready={s.ready}"
            )

        print(f"Schreinerei Slots: {len(forestry_svc.carpentry.slots)}")
        for s_id, s in forestry_svc.carpentry.slots.items():
            print(
                f"  - Slot {s_id}: Busy={s.busy}, PID={s.productid}, Remain={s.remain}s, Ready={s.ready}"
            )

        # In-stock wood products
        wood_in_stock = [p for p in forestry_svc.products.values() if p.amount > 0]
        print(f"Holzlager ({len(wood_in_stock)} Positionen mit Bestand):")
        for wp in wood_in_stock[:8]:
            print(f"  - {wp.name} (PID {wp.pid}, Kat {wp.category}): {wp.amount} Stk")

        # Forest Farmis
        print(f"Waldhuette Kunden (Farmis): {len(forestry_svc.farmis.farmis)}")
        for farmi in forestry_svc.farmis.farmis:
            req_str = ", ".join(f"{amt}x PID {pid}" for pid, amt in farmi.products.items())
            print(
                f"  - Farmi #{farmi.farmi_id} (Pos {farmi.position}): {req_str} -> {farmi.reward_points} Punkte, {farmi.reward_money:.2f} kT"
            )

        # 4. Quest Service Inspection
        print("\n--- 2. Quest-Status ---")
        quest_svc = QuestService(client, stock_service=stock_service)
        quest = await quest_svc.fetch_quest_status()

        if quest:
            print(
                f"Aktive Quest #{quest.quest_nr}: '{quest.title}' (Bereit zur Abgabe: {quest.is_ready})"
            )
            for req in quest.requirements:
                print(
                    f"  - {req.name} (PID {req.pid}): "
                    f"Bedarf={req.amount_needed}, Bestand={req.current_stock}, Fehlmenge={req.missing}"
                )
        else:
            print("Keine aktive Hauptquest ermittelt.")

        # 5. Helpers & Daily Bonuses Inspection
        print("\n--- 3. Tägliche Helfer & Boni ---")
        farms_data = await client.api_call("farm", {"mode": "getfarms", "farm": 1, "position": 0})
        menue = farms_data.get("updateblock", {}).get("menue", {})

        farmdog_harvest = bool(menue.get("farmdog_harvest"))
        print(
            f"Braver Ben (10-Min-Boost): {'Bereits abgeholt' if farmdog_harvest else 'Bereit zur Abholung'}"
        )

        donkey_ready = int(menue.get("donkey", 0)) == 1
        print(
            f"Goldesel Waltraud (kT-Münzen): {'Bereit zum Melken' if donkey_ready else 'Heute nicht bereit'}"
        )

        lottery_data = await client.api_call("city", {"mode": "initlottery", "city": 2})
        lottery_datablock = lottery_data.get("datablock", [])
        lottery_waiting = len(lottery_datablock) > 2 and lottery_datablock[2] == 0
        print(
            f"Losbude Dorf 2: {'Gratis-Los verfuegbar!' if lottery_waiting else 'Heute bereits abgeholt'}"
        )

        windmill_data = await client.api_call("city", {"mode": "windmillinit", "city": 2})
        wm_datablock = windmill_data.get("datablock", [])
        if len(wm_datablock) > 4 and isinstance(wm_datablock[4], dict):
            ready = wm_datablock[4].get("ready", 0)
            running = wm_datablock[4].get("running", 0)
            slot = {}
            if len(wm_datablock) > 2:
                raw_slots = wm_datablock[2]
                if isinstance(raw_slots, dict):
                    slot = raw_slots.get("1") or raw_slots.get(1) or {}
                elif isinstance(raw_slots, list):
                    if len(raw_slots) > 1 and isinstance(raw_slots[1], dict):
                        slot = raw_slots[1]
                    elif len(raw_slots) > 0 and isinstance(raw_slots[0], dict):
                        slot = raw_slots[0]
            print(
                f"Windmuehle: Ready={ready}, Running={running}, Slot 1 Remain={slot.get('remain', 0)}s"
            )
        # 6. Biosprit-Anlage Inspection (Farm 4, Pos 6)
        print("\n--- 4. Biosprit-Anlage (Farm 4, Pos 6) ---")
        farms_data_4 = await client.api_call("farm", {"mode": "getfarms", "farm": 4, "position": 0})
        b20_data = (
            farms_data_4.get("updateblock", {})
            .get("farms", {})
            .get("farms", {})
            .get("4", {})
            .get("6", {})
        )
        if b20_data.get("buildingid") == "20":
            fs = Fuelstation(client, farm_id=4, position=6)
            fs.update(b20_data)
            print(f"Biosprit-Anlage: Level {fs.level} | Kontostand: {fs.tokens:,} Biosprit-Marken")
            for s_id, slot in fs.slots.items():
                if slot.is_blocked:
                    status_str = "Gesperrt (Freischaltung erfordert Coins)"
                elif slot.is_finished:
                    status_str = "Fertig zur Ernte!"
                elif slot.busy:
                    status_str = f"In Produktion (Rest: {slot.remain}s)"
                else:
                    status_str = f"Bereit zum Befüllen (noch {slot.points_left} Pkt)"
                print(f"  - Slot {s_id}: Level {slot.level} | {status_str}")
        else:
            print("Keine Biosprit-Anlage auf Farm 4, Pos 6 gefunden.")

        print(f"\n{'=' * 75}\n")

    finally:
        await client.logout()
