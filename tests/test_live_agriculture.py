import pytest

from app.config import settings
from app.core.client import MFFGameClient
from app.modules.farm_buildings.farm_service import FarmService
from app.services.stock_service import StockService


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_agriculture_and_barns_inspection():
    """Live Read-Only Inspection Test for Phase 3 against MyFreeFarm Server.

    Discovers all farms, fields, and animal sheds, inspects their current status
    (growing plants, water status, animals, remaining timers) and displays a
    formatted summary on the console without making any state-changing actions.
    Run with: uv run pytest tests/test_live_agriculture.py -s
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

        # 2. Init stock service for product display names
        main_page = await client.client.get(f"https://s{client.server}.myfreefarm.de/main.php")
        stock_service = StockService(client)
        await stock_service.init(main_page.text)

        # 3. Discover buildings via FarmService
        farm_service = FarmService(client)
        await farm_service.update_farms()

        print(f"\n{'=' * 75}")
        print(f"MyFreeFarm Live Farm-Inspektion (Server {client.server}, User '{client.username}')")
        print(
            f"Gefundene Aecker: {len(farm_service.fields)} | Gefundene Staelle: {len(farm_service.sheds)}"
        )
        print(f"{'=' * 75}")

        # 4. Inspect Fields (Read-Only)
        print("\n--- Aecker & Felder ---")
        for f in farm_service.fields:
            await f.update()
            planted_count = len(f.tiles)
            ready_count = f.ready_crops_count
            unwatered = f.unwatered_count

            # Determine dominant crop
            dominant_crop = "Leer"
            if f.tiles:
                p_sample = stock_service.get_product(f.tiles[0].pid)
                p_name = p_sample.name if p_sample else f"PID {f.tiles[0].pid}"
                min_remain = min(t.remain_seconds for t in f.tiles)
                dominant_crop = f"{p_name} (Rest: {min_remain // 60}m {min_remain % 60}s)"

            print(
                f"  Farm {f.farm_id}, Pos {f.position} ({f.name:<12}): "
                f"{planted_count:>3}/120 belegt | "
                f"{ready_count:>3} erntereif | "
                f"{unwatered:>3} unbewaessert | "
                f"{dominant_crop}"
            )

        # 5. Inspect Sheds (Read-Only)
        print("\n--- Tierstaelle ---")
        for s in farm_service.sheds:
            await s.update()
            if s.barn:
                prod_item = stock_service.get_product(s.barn.product_id)
                prod_name = prod_item.name if prod_item else f"PID {s.barn.product_id}"
                rem_m = s.barn.remain_seconds // 60
                rem_s = s.barn.remain_seconds % 60
                feed_count = len(s.barn.feed_options)

                print(
                    f"  Farm {s.farm_id}, Pos {s.position} ({s.name:<12}): "
                    f"{s.barn.animals_count:>2} Tiere | "
                    f"Erzeugt: {prod_name:<10} | "
                    f"Restzeit: {rem_m:>3}m {rem_s:>2}s | "
                    f"{feed_count} Futteroptionen"
                )
            else:
                print(
                    f"  Farm {s.farm_id}, Pos {s.position} ({s.name:<12}): "
                    f"Unbesetzt (Keine Tiere im Stall)"
                )

        print(f"\n{'=' * 75}\n")

    finally:
        await client.logout()
