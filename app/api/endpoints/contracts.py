from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger

from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

contracts_router = APIRouter(prefix="/contracts", tags=["Contracts"])


@contracts_router.get("", response_model=dict[str, Any])
async def get_contracts(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve player contracts enriched with current inventory and readiness status."""
    stock = worker_scheduler.last_stock_service
    enriched_contracts: dict[str, list[dict[str, Any]]] = {}
    contracts_store = settings_manager.get_contracts()

    for user, contract_list in contracts_store.items():
        enriched_list = []
        for contract in contract_list:
            c = dict(contract)
            pid = c.get("pid")
            amount = c.get("amount", 0)
            min_reserve = c.get("min", 0)

            product = stock.get_product(pid) if (stock and pid) else None
            in_stock = product.total_amount if product else 0
            catalog_price = product.price if product else 1.0

            c["inStock"] = in_stock
            c["stockPrice"] = catalog_price
            c["ready"] = in_stock >= (amount + min_reserve)
            enriched_list.append(c)
        enriched_contracts[user] = enriched_list

    return enriched_contracts


@contracts_router.put("")
async def update_contracts(
    payload: dict[str, list[dict[str, Any]]] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update player contract settings and persist to disk."""
    settings_manager.set_contracts(payload)
    logger.info(f"API: Verträge aktualisiert und persistiert: {len(payload)} Partner hinterlegt.")
    return {"status": "ok", "contracts": payload}
