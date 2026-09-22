from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger

from app.config import TradeRule, settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

offers_router = APIRouter(prefix="/offers", tags=["Marketplace & Offers"])


@offers_router.get("", response_model=list[dict[str, Any]])
async def get_offers(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve active marketplace sales rules enriched with product details."""
    stock = worker_scheduler.last_stock_service
    result = []
    for rule in settings.trade.sell_rules:
        plant_info = None
        if stock:
            p = stock.get_product(rule.pid)
            if p:
                plant_info = {
                    "pid": p.pid,
                    "name": p.name,
                    "price": p.price,
                    "amount": p.amount,
                }
        result.append({
            "pid": rule.pid,
            "minReserve": rule.min_reserve,
            "sellBatch": rule.sell_batch,
            "targetPrice": rule.target_price,
            "plant": plant_info,
        })
    return result


@offers_router.put("")
async def update_offers(
    payload: Any = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update marketplace sales rules and trading configuration."""
    if isinstance(payload, list):
        new_rules = []
        for item in payload:
            if isinstance(item, dict) and "pid" in item:
                new_rules.append(
                    TradeRule(
                        pid=int(item["pid"]),
                        min_reserve=int(item.get("minReserve") or item.get("min_reserve", 1000)),
                        sell_batch=int(item.get("sellBatch") or item.get("sell_batch", 100)),
                        target_price=float(item["targetPrice"]) if item.get("targetPrice") else None,
                    )
                )
        settings.trade.sell_rules = new_rules
    elif isinstance(payload, dict):
        if "enabled" in payload:
            settings.trade.enabled = bool(payload["enabled"])
        if "minCreditKt" in payload or "min_credit_kt" in payload:
            settings.trade.min_credit_kt = float(payload.get("minCreditKt") or payload.get("min_credit_kt"))
        if "autoSellSurplus" in payload or "auto_sell_surplus" in payload:
            settings.trade.auto_sell_surplus = bool(payload.get("autoSellSurplus") or payload.get("auto_sell_surplus"))
        if "sellCategoryV" in payload or "sell_category_v" in payload:
            val = payload.get("sellCategoryV") if "sellCategoryV" in payload else payload.get("sell_category_v")
            settings.trade.sell_category_v = bool(val)
        if "excludeCategories" in payload or "exclude_categories" in payload:
            cats = payload.get("excludeCategories") or payload.get("exclude_categories")
            if isinstance(cats, list):
                settings.trade.exclude_categories = [str(c) for c in cats]

    settings_manager.save()
    logger.info(f"API: Handelseinstellungen aktualisiert: {settings.trade}")
    return {"status": "ok", "trade_config": settings.trade.model_dump()}
