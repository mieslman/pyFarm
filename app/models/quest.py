from pydantic import BaseModel, Field


class QuestRequirement(BaseModel):
    """Product requirement for an active quest."""

    pid: int
    name: str
    amount_needed: int
    current_stock: int = 0
    missing: int = 0


class QuestStatus(BaseModel):
    """Current quest progress and status."""

    quest_nr: int
    title: str = ""
    requirements: list[QuestRequirement] = Field(default_factory=list)
    is_ready: bool = False
