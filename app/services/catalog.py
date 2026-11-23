import json
import re
from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.models.product import Product

# In-memory template catalog cache keyed by versioned jsconstants_*.js URL.
# Each entry stores pristine Product templates (amount=0, tmp_amount=0).
_catalog_cache: dict[str, dict[int, Product]] = {}


def clear_catalog_cache() -> None:
    """Clear the cached product catalogs."""
    _catalog_cache.clear()


def parse_js_dict(text: str, var_name: str) -> dict[int, Any]:
    """Extract a JavaScript object literal (var var_name = {...};) and parse it as a dict."""
    pattern = rf"var\s+{re.escape(var_name)}\s*=\s*(\{{.*?\}});"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        logger.warning(f"Konnte Variable '{var_name}' im Skript nicht finden.")
        return {}

    raw_json = match.group(1)
    # Normalize unquoted integer keys (e.g. {1: -> {"1":)
    cleaned = re.sub(r"(?<=[{,])\s*(\d+)\s*:", r'"\1":', raw_json)
    # Normalize single quotes to double quotes
    cleaned = re.sub(r"'([^']*)'", r'"\1"', cleaned)
    # Remove trailing commas before closing braces
    cleaned = re.sub(r",\s*\}", "}", cleaned)

    try:
        data = json.loads(cleaned)
        # Convert string PID keys to integer
        return {int(k): v for k, v in data.items() if str(k).isdigit()}
    except json.JSONDecodeError as e:
        logger.error(f"Fehler beim Parsen von '{var_name}': {e} in {raw_json[:100]}...")
        return {}


def extract_constants_url(html_content: str, server: int) -> str:
    """Find the referenced jsconstants_*.js file URL in the game HTML."""
    match = re.search(r'["\']([^"\']*jsconstants_\d+\.js)["\']', html_content)
    if match:
        relative_or_abs = match.group(1)
        if relative_or_abs.startswith("http"):
            return relative_or_abs
        cleaned = relative_or_abs.lstrip("/")
        return f"https://s{server}.myfreefarm.de/{cleaned}"

    # Fallback to known default constants file
    return f"https://s{server}.myfreefarm.de/js/jsconstants_241014.js"


def build_catalog(html_content: str, js_constants_content: str) -> dict[int, Product]:
    """Parse HTML and JS constants into a unified dictionary of Product models."""
    names = parse_js_dict(html_content, "produkt_name")
    prices = parse_js_dict(html_content, "produkt_price")
    sizes_x = parse_js_dict(js_constants_content, "produkt_x")
    sizes_y = parse_js_dict(js_constants_content, "produkt_y")
    categories = parse_js_dict(js_constants_content, "produkt_category")

    catalog: dict[int, Product] = {}
    for pid, name in names.items():
        price = float(prices.get(pid, 0.0))
        sx = int(sizes_x.get(pid, 1))
        sy = int(sizes_y.get(pid, 1))
        cat = str(categories.get(pid, "v"))

        catalog[pid] = Product(
            pid=pid,
            name=name,
            price=price,
            size_x=sx,
            size_y=sy,
            category=cat,
            amount=0,
            tmp_amount=0,
        )

    logger.info(f"Produktkatalog erfolgreich initialisiert: {len(catalog)} Produkte geladen.")
    return catalog


async def load_remote_catalog(
    client: MFFGameClient, login_html: str, *, use_cache: bool = True
) -> dict[int, Product]:
    """Fetch external JS constants and assemble the full product catalog.

    Caches the assembled catalog per versioned ``jsconstants_*.js`` URL so subsequent
    scheduler cycles avoid redundant HTTP downloads and JS parsing. Always returns
    fresh ``Product`` copies so in-memory stock mutations never leak into the cache.
    """
    constants_url = extract_constants_url(login_html, client.server)

    if use_cache and constants_url in _catalog_cache:
        cached = _catalog_cache[constants_url]
        logger.debug(
            f"Verwende gecachten Produktkatalog für {constants_url} ({len(cached)} Produkte)."
        )
        return {pid: product.model_copy() for pid, product in cached.items()}

    logger.debug(f"Lade JS-Konstanten von: {constants_url}")

    res = await client.client.get(constants_url)
    if res.status_code != 200:
        logger.error(f"Konnte JS-Konstanten nicht laden (HTTP {res.status_code}).")
        constants_text = ""
    else:
        constants_text = res.text

    catalog = build_catalog(login_html, constants_text)
    if res.status_code == 200 and catalog:
        _catalog_cache[constants_url] = {
            pid: product.model_copy() for pid, product in catalog.items()
        }

    return catalog
