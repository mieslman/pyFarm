from pydantic import BaseModel, Field


class Product(BaseModel):
    """Clean domain model for farm products, plants, animals, and goods."""

    pid: int = Field(description="Unique Product ID")
    name: str = Field(description="Display name of the product")
    price: float = Field(default=0.0, description="Base NPC / system price in kT")
    size_x: int = Field(default=1, description="Grid width requirement (1 or 2)")
    size_y: int = Field(default=1, description="Grid height requirement (1 or 2)")
    category: str = Field(
        default="v", description="Category identifier (e.g. v=plants, t=tier, etc.)"
    )
    amount: int = Field(default=0, description="Current stock in main inventory")
    tmp_amount: int = Field(default=0, description="Temporary or contracted amount")

    @property
    def total_amount(self) -> int:
        return self.amount + self.tmp_amount

    @property
    def is_multi_tile(self) -> bool:
        return self.size_x > 1 or self.size_y > 1


class MarketOffer(BaseModel):
    """Represents an active player offer on the marketplace."""

    offer_id: int = Field(description="Unique Offer ID on marketplace")
    pid: int = Field(description="Product ID")
    amount: int = Field(description="Available quantity in offer")
    price: float = Field(description="Price per unit in kT")
