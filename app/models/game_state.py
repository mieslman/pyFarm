from pydantic import BaseModel, Field

from app.models.farm import FarmField
from app.models.product import Product


class GameState(BaseModel):
    """In-memory snapshot of a player's farm state on a specific server."""

    server_id: int
    username: str
    credits_kt: float = 0.0
    coins: int = 0
    level: int = 1
    farms: dict[int, list[FarmField]] = Field(default_factory=dict)
    stock: dict[int, Product] = Field(default_factory=dict)
    last_updated: float = 0.0
