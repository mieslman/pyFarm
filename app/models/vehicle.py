from pydantic import BaseModel, Field


class VehicleState(BaseModel):
    """Current state of a vehicle as reported by updateblock.map.vehicles."""

    current: int = Field(description="Current farm location (e.g. 1 for main farm, 5, 6, 8, 10)")
    route: int = Field(description="Assigned route ID")
    vehicle_type: int = Field(alias="type", description="Vehicle type ID (e.g. 4 for tractor, 9 for pickup)")
    remain: int = Field(default=0, description="Transit cooldown in seconds (0 = idle/ready)")

    model_config = {"populate_by_name": True}


class VehicleConfigData(BaseModel):
    """Static vehicle specification as reported by updateblock.map.config.vehicles."""

    name: str = Field(description="Display name of the vehicle (e.g. Traktor, Pickup)")
    capacity: int = Field(default=500, description="Maximum cargo capacity in units")
    products: int = Field(default=2, description="Maximum distinct product cargo slots")
    farms: list[int] = Field(default_factory=list, description="Target outer farm IDs")
    duration: int = Field(default=0, description="Standard travel duration in seconds")
    speed: float = Field(default=1.0, description="Speed multiplier if present")


class VehicleRouteConfig(BaseModel):
    """User configuration for a vehicle logistics route."""

    farm_id: int = Field(description="Target outer farm ID (e.g. 5, 6, 8, 10)")
    route: int = Field(description="Route ID (e.g. 1, 2, 4)")
    vehicle: int | None = Field(default=None, description="Specific vehicle type ID, or None for auto-fastest")
    auto_fastest: bool = Field(default=True, description="Always select the fastest available vehicle on this route")
    transport: bool = Field(default=True, description="Enable automatic transport to/from this farm")
    required_products: list[str | int] = Field(
        default_factory=list,
        description="Products to supply from main farm to this outer farm (names or PIDs)",
    )
    send_partial: bool = Field(
        default=False,
        description="Send vehicle back even if capacity is not fully loaded",
    )
    prioritize_quests: bool = Field(
        default=True,
        description="Prioritize products demanded by active quests when loading",
    )
    only_milled_surplus: bool = Field(
        default=False,
        description="On Farm 10, only load milled products as general harvest surplus",
    )
    only_quest_products: bool = Field(
        default=False,
        description="Only transport products that are actively demanded by quests to the main farm",
    )
    sushi_supply: bool = Field(
        default=False,
        description="On Farm 8, automatically supply ingredients needed for Sushi-Bar recipes if tempstock < threshold",
    )
    sushi_reserve_threshold: int = Field(
        default=500,
        description="Threshold below which Sushi-Bar ingredients are supplied from main farm",
    )
    min_crop_reserve: int = Field(
        default=0,
        description="Minimum crop safety reserve to keep on outer farm (e.g. 500 for water plants on Farm 8)",
    )


class VehiclesConfig(BaseModel):
    """Overall vehicle logistics settings."""

    enabled: bool = True
    routes: dict[int, VehicleRouteConfig] = Field(
        default_factory=lambda: {
            5: VehicleRouteConfig(
                farm_id=5,
                route=1,
                vehicle=4,
                auto_fastest=True,
                transport=True,
                required_products=["Kohlrabi", "Wollknäuel"],
                prioritize_quests=True,
            ),
            6: VehicleRouteConfig(
                farm_id=6,
                route=2,
                vehicle=9,
                auto_fastest=True,
                transport=True,
                required_products=[],
                prioritize_quests=True,
            ),
            8: VehicleRouteConfig(
                farm_id=8,
                route=4,
                vehicle=19,
                auto_fastest=True,
                transport=True,
                required_products=[],
                prioritize_quests=True,
                only_quest_products=True,
                sushi_supply=True,
                sushi_reserve_threshold=500,
                min_crop_reserve=500,
            ),
            10: VehicleRouteConfig(
                farm_id=10,
                route=6,
                vehicle=29,
                auto_fastest=True,
                transport=False,
                required_products=[],
                prioritize_quests=True,
                only_milled_surplus=True,
            ),
        }
    )


class VehicleCargoItem(BaseModel):
    """Single cargo slot item in vehicle cart."""

    slot: int
    pid: int
    name: str
    amount: int
    is_quest_product: bool = False


class VehicleInfo(BaseModel):
    """DTO for REST-API and Dashboard."""

    route_id: int
    vehicle_id: int
    target_farm_id: int
    name: str
    capacity: int
    products_slots: int
    duration: int
    current_location: int
    remain_seconds: int
    status: str  # "ready_main", "ready_outer", "driving", "waiting_outer", "disabled"
    transport_enabled: bool
    required_products: list[str | int]
    cargo: list[VehicleCargoItem] = Field(default_factory=list)
    last_sent_cart: str = ""
    only_quest_products: bool = False
    sushi_supply: bool = False

