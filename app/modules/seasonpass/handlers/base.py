from abc import ABC, abstractmethod
from typing import Any
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.seasonpass.models import SeasonPassConfig, SeasonPassTask
from app.services.stock_service import StockService


class BaseTaskHandler(ABC):
    """Abstract base class for all Seasonpass task handlers."""

    task_type: str = "base"

    def __init__(
        self,
        task: SeasonPassTask,
        client: MFFGameClient,
        stock_service: StockService | None = None,
        farm_service: Any | None = None,
        config: SeasonPassConfig | None = None,
    ):
        self.task = task
        self.client = client
        self.stock_service = stock_service
        self.farm_service = farm_service
        self.config = config or SeasonPassConfig()
        self.logger = logger

    @abstractmethod
    async def run(self) -> bool:
        """Execute the task actions against the upstream game server."""
        raise NotImplementedError
