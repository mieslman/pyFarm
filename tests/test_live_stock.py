import pytest

from app.config import settings
from app.core.client import MFFGameClient
from app.services.stock_service import StockService


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_stock_inventory_overview():
    """Live End-to-End Test for Phase 2 against MyFreeFarm game server.

    Connects with credentials from .env, parses the live product catalog,
    and displays an overview of current stock levels and account credit.
    Run with: uv run pytest tests/test_live_stock.py -s
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

        # 2. Fetch login page body to extract catalog arrays
        # The client already retrieved redirect page during login; we can get main.php:
        main_page = await client.client.get(f"https://s{client.server}.myfreefarm.de/main.php")

        stock_service = StockService(client)
        await stock_service.init(main_page.text)

        # 3. Assertions
        assert len(stock_service.products) > 0, "Produktkatalog darf nicht leer sein"
        print(f"\n{'=' * 65}")
        print(f"MyFreeFarm Live-Lagerbestand (Server {client.server})")
        print(f"Kontostand: {stock_service.credits_kt:.2f} kT")
        print(f"Gesamte Produkte im Katalog: {len(stock_service.products)}")
        print(f"{'=' * 65}")
        print(f"{'PID':<6} | {'Produkt':<20} | {'Lager':<8} | {'Temp':<6} | {'Preis (kT)':<10}")
        print(f"{'-' * 65}")

        items_in_stock = [
            p for p in stock_service.products.values() if p.amount > 0 or p.tmp_amount > 0
        ]
        items_in_stock.sort(key=lambda x: x.amount + x.tmp_amount, reverse=True)

        for p in items_in_stock:
            print(
                f"{p.pid:<6} | {p.name:<20} | {p.amount:<8} | {p.tmp_amount:<6} | {p.price:<10.2f}"
            )

        print(f"{'=' * 65}")
        print(f"Gefundene Produkte im Lager: {len(items_in_stock)}")
        print(f"{'=' * 65}\n")

    finally:
        await client.logout()
