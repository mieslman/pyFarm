from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.vehicle import VehiclesConfig


class FarmStrategyConfig(BaseModel):
    """Field automation strategy settings."""

    enabled: bool = True
    plant_strategy: str = "plantQuest"  # plantQuest, plantMin, plantOrders
    plant_category: str = "v"
    min_products: int = 500
    auto_water: bool = True
    auto_crop: bool = True
    harvest_mode: str = "all"  # "all" = harvest only when all ready, "any" = harvest partial
    farm_crops: dict[int, int] = Field(default_factory=lambda: {1: 8, 3: 8, 4: 113})
    farm_categories: dict[int, str] = Field(
        default_factory=lambda: {
            5: "ex",
            6: "alpin",
            8: "water",
            10: "spice",
        }
    )

    def get_farm_category(self, farm_id: int) -> str:
        """Return the required crop category for a specific farm (e.g. 5: ex, 6: alpin, 8: water, 10: spice)."""
        return self.farm_categories.get(farm_id, self.plant_category)


class TradeRule(BaseModel):
    """Specific product trade rule."""

    pid: int
    min_reserve: int = 1000
    sell_batch: int = 100
    target_price: float | None = None


class TradeConfig(BaseModel):
    """Automatic trade & marketplace settings."""

    enabled: bool = False
    min_credit_kt: float = 500.0
    underbid_offset_kt: float = 0.01
    default_reserve: int = 1000
    auto_sell_surplus: bool = False
    sell_category_v: bool = False  # Verkaufe keine normalen Produkte (Kategorie "v") auf dem Markt
    exclude_categories: list[str] = Field(default_factory=lambda: ["v"])
    sell_rules: list[TradeRule] = []


class ForestryConfig(BaseModel):
    """Forestry (Baumerei) settings."""

    enabled: bool = True
    auto_cut: bool = True
    auto_plant: bool = True
    auto_water: bool = True
    auto_produce: bool = True
    serve_farmis: bool = True


class QuestConfig(BaseModel):
    """Quest tracking & solver settings."""

    enabled: bool = True
    quest_nr: int | None = None
    auto_buy_missing: bool = False


class HelpersConfig(BaseModel):
    """Daily bonus and helper settings."""

    enabled: bool = True
    farm_dog: bool = True
    donkey: bool = True
    lottery: bool = True
    windmill: bool = True
    loginbonus: bool = True
    greenhouse: bool = True
    pet_parts: bool = True


class FormulaDealerConfig(BaseModel):
    """Bauplan- & Formel-Händler (Stadt 2) and powerup activation settings."""

    enabled: bool = True
    auto_activate_powerups: bool = True
    auto_buy_formulas: bool = True
    formula_min: int = 20
    required_formulas: list[str] = Field(
        default_factory=lambda: [
            "Zucchiniauflauf",
            "Apfelkompott",
            "Erntedankfigur",
            "Fischfutter, günstig",
            "Pferdefutter Korn-Karotten",
            "Pferdefutter Kräuter-Kartoffeln",
        ]
    )


class FuelstationConfig(BaseModel):
    """Biosprit-Anlage (Fuelstation) automation settings."""

    enabled: bool = True
    auto_harvest: bool = True
    auto_refill: bool = True
    min_reserve: int = 500
    preferred_pids: list[int] = Field(default_factory=lambda: [2, 18, 17, 1])
    slot_preferred_pids: dict[int, list[int]] = Field(
        default_factory=lambda: {
            1: [2, 18, 17, 1],   # Slot 1 (Stufe 5): Mais (2) bevorzugt, dann Gurken (18)
            2: [18, 17, 1],       # Slot 2 (Stufe 3): Gurken (18) bevorzugt (Mais erst ab Stufe 5)
            3: [17, 113, 33, 31], # Slot 3 (Stufe 1): Karotten (17), Chili, Himbeeren, Zucchini
            4: [17, 113, 33, 31], # Slot 4 (Stufe 1)
        }
    )


class FarmersMarketConfig(BaseModel):
    """Farmersmarket (Dorf 2) automation settings."""

    enabled: bool = True
    nursery_enabled: bool = True
    flower_area_enabled: bool = True
    flower_slots_enabled: bool = True
    farmis_enabled: bool = True
    pet_breed_enabled: bool = False  # Vorgegeben: Inaktiv
    pet_daily_parts: bool = True


class FoodworldConfig(BaseModel):
    """Foodworld (Picknick-Bereich / Restaurant) automation settings."""

    enabled: bool = True
    auto_cook: bool = True
    auto_seat_guests: bool = True
    auto_cash_tables: bool = True
    auto_buy_ingredients: bool = True  # Automatische Rohstoffbeschaffung via Markt/Saatguthändler
    dish_reserve_buffer: int = 50  # Mindestbestand von 50 pro Speise vorhalten
    market_export_enabled: bool = True  # Überschuss (> 50) am Markt anbieten
    only_empty_market: bool = True  # Nur anbieten, wenn bisher kein Angebot dafür vorhanden ist
    auto_unlock_tables: bool = False  # Keine automatischen Tischkäufe tätigen



class SushibarConfig(BaseModel):
    """Sushi-Bar (Farm 8, Position 2) automation settings."""

    enabled: bool = True
    auto_harvest: bool = True
    auto_produce: bool = True
    auto_train: bool = False  # Standardmäßig inaktiv (Nutzer-Vorgabe)
    auto_farmi: bool = True
    production_strategy: str = "quest5"  # "quest5", "balanced", "preferred"
    preferred_pids: list[int] = Field(default_factory=list)
    reserve_full_field: bool = True  # Mindestreserve: 120 // (size_x * size_y)
    coin_protection: bool = True  # Strikter Schutz: Keine Coin-Rezepte, kein Coin-Speedup


class FactoryConfig(BaseModel):
    """Processing factories (Veredelungsbetriebe: Käserei, Ölpresse, Spinnerei etc.) settings."""

    enabled: bool = True
    auto_harvest: bool = True
    auto_produce: bool = True
    coin_protection: bool = True


class SpicehouseSettingsConfig(BaseModel):
    """Gewürzhaus (Farm 10, Position 2) settings."""

    enabled: bool = True
    auto_oven: bool = True
    auto_mill: bool = True
    auto_customer: bool = True
    min_crop_reserve: int = 500


class SeasonpassSettingsConfig(BaseModel):
    """Seasonpass (Saisonale Reise) automation settings."""

    enabled: bool = True
    auto_claim_rewards: bool = True
    friend_unr: str = ""
    preferred_field_farm: int = 1
    preferred_field_pos: int = 1
    preferred_forestry_pos: int = 1


class EventsSettingsConfig(BaseModel):
    """Seasonal and temporary events automation settings."""

    enabled: bool = True
    auto_detect: bool = True
    calendar_enabled: bool = True
    delivery_enabled: bool = True
    eventgarden_enabled: bool = True
    oktoberfest_enabled: bool = True
    pentecost_enabled: bool = True
    olympia_enabled: bool = True


class InsecthotelSettingsConfig(BaseModel):
    """Insect hotel automation settings."""

    enabled: bool = True
    auto_refill_stock: bool = True
    refill_threshold_percent: float = 0.2
    auto_collect_checkout: bool = True
    checkout_threshold_percent: float = 0.5
    min_stock_reserve: int = 50
    auto_buy_feed: bool = True


class StallSettingsConfig(BaseModel):
    """Fruit market stall (Obststand / Marktbude) automation settings."""

    enabled: bool = True
    auto_clear_depleted: bool = True
    clear_threshold_percent: float = 0.5
    auto_fill_slots: bool = True
    auto_collect_reward: bool = True
    min_stock_reserve: int = 500


class StockConfig(BaseModel):
    """Inventory management and raw materials grasping settings."""

    buffer_crops: int = 500
    buffer_other: int = 5
    max_price_factor_crops: float = 1.0
    max_price_factor_other: float = 3.0
    min_credit_kt: float = 500.0


class AccountConfig(BaseModel):
    """MyFreeFarm credentials and server settings."""

    server: int = 1
    username: str = ""
    password: str = ""


class Settings(BaseSettings):
    """Application settings loaded from .env or environment variables."""

    app_name: str = "MyFreeFarm Engine"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # Server & Web
    host: str = "0.0.0.0"
    port: int = 8000

    # Security & API Auth
    secret_key: str = "change-this-in-production-super-secret-key-32chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    api_username: str = "mff"
    api_password: str = "M3rh4b4@mff"

    # Worker & Scheduling
    poll_interval_seconds: int = 600  # 10 minutes loop
    anti_detection_min_delay_ms: int = 350
    anti_detection_max_delay_ms: int = 900

    # Game Account & Automation Defaults
    account: AccountConfig = Field(default_factory=AccountConfig)
    agriculture: FarmStrategyConfig = Field(default_factory=FarmStrategyConfig)
    trade: TradeConfig = Field(default_factory=TradeConfig)
    stock: StockConfig = Field(default_factory=StockConfig)
    forestry: ForestryConfig = Field(default_factory=ForestryConfig)
    quest: QuestConfig = Field(default_factory=QuestConfig)
    helpers: HelpersConfig = Field(default_factory=HelpersConfig)
    formula_dealer: FormulaDealerConfig = Field(default_factory=FormulaDealerConfig)
    fuelstation: FuelstationConfig = Field(default_factory=FuelstationConfig)
    farmersmarket: FarmersMarketConfig = Field(default_factory=FarmersMarketConfig)
    foodworld: FoodworldConfig = Field(default_factory=FoodworldConfig)
    sushibar: SushibarConfig = Field(default_factory=SushibarConfig)
    factories: FactoryConfig = Field(default_factory=FactoryConfig)
    spicehouse: SpicehouseSettingsConfig = Field(default_factory=SpicehouseSettingsConfig)
    seasonpass: SeasonpassSettingsConfig = Field(default_factory=SeasonpassSettingsConfig)
    events: EventsSettingsConfig = Field(default_factory=EventsSettingsConfig)
    insecthotel: InsecthotelSettingsConfig = Field(default_factory=InsecthotelSettingsConfig)
    stall: StallSettingsConfig = Field(default_factory=StallSettingsConfig)
    vehicles: VehiclesConfig = Field(default_factory=VehiclesConfig)


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MFF_",
        env_nested_delimiter="__",
        extra="ignore",
    )


settings = Settings()
