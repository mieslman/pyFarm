from typing import Any

from loguru import logger

from app.config import VehiclesConfig, settings
from app.core.client import MFFGameClient
from app.models.product import Product
from app.models.vehicle import (
    VehicleCargoItem,
    VehicleConfigData,
    VehicleInfo,
    VehicleRouteConfig,
    VehicleState,
)
from app.services.stock_service import StockService


def resolve_pid(item: str | int, stock_service: StockService | None = None) -> int | None:
    """Resolve a product identifier (PID or name) to a numeric PID."""
    if isinstance(item, int):
        return item
    if str(item).isdigit():
        return int(item)
    if stock_service:
        p = stock_service.get_product_by_name(str(item))
        if p:
            return p.pid
    return None


def calculate_speed_score(v_cfg: VehicleConfigData, v_id: int) -> float:
    """Calculate speed rating for a vehicle type (higher score = faster)."""
    # 1. Travel duration: lower duration is faster
    if v_cfg.duration > 0:
        return 10_000_000.0 / float(v_cfg.duration)
    # 2. Speed factor: higher is faster
    if v_cfg.speed > 1.0:
        return float(v_cfg.speed) * 1000.0
    # 3. Fallback: higher capacity and vehicle tier indicate faster vehicle
    return float(v_cfg.capacity) * 10.0 + float(v_id)


# Fallback known non-coin Sushi-Bar ingredient PIDs (from upstream recipe definitions)
FALLBACK_SUSHIBAR_INGREDIENTS = {9, 12, 17, 19, 22, 26, 34, 42, 112, 115}


def get_sushibar_needed_ingredients(
    sushibar_service: Any | None = None,
    stock_service: StockService | None = None,
) -> set[int]:
    """Extract all ingredient product IDs required by active non-coin Sushi-Bar recipes,
    excluding water plants and sushi dishes that originate locally on Farm 8.
    """
    ingredients: set[int] = set()
    if sushibar_service and getattr(sushibar_service, "recipes", None):
        level = getattr(sushibar_service, "level", 1)
        for r in sushibar_service.recipes.values():
            if r.level <= level and not r.is_coin_recipe:
                for nid in r.needs.keys():
                    pid_int = int(nid)
                    if stock_service:
                        prod = stock_service.get_product(pid_int)
                        if prod and prod.category in ("water", "sushi", "soup", "salad", "dessert"):
                            continue
                    elif 950 <= pid_int < 999:  # Water plants & sushi dishes
                        continue
                    ingredients.add(pid_int)
    if not ingredients:
        ingredients = set(FALLBACK_SUSHIBAR_INGREDIENTS)
    return ingredients



def is_product_for_farm(product: Product, farm_id: int) -> bool:
    """Determine if a product can originate from or be harvested/produced on a specific outer farm."""
    farm_cat = settings.agriculture.get_farm_category(farm_id)
    if farm_id == 8:
        # Farm 8: Water farm (Teich) -> water plants and sushi dishes
        return (
            product.category in ("water", "sushi", "soup", "salad", "dessert")
            or product.category == farm_cat
        )
    if farm_id == 10:
        # Farm 10: Kräutergarten & Ölmühle -> spices, oils, herbs, and milled products
        return (
            product.category in ("spice", "oil")
            or product.category == farm_cat
            or "gemahlen" in product.name.lower()
        )
    if farm_id == 6:
        # Farm 6: Alpinfarm -> alpine crops
        return product.category in ("alpin",) or product.category == farm_cat
    if farm_id == 5:
        # Farm 5: Tierfarm / Picknick -> exotic plants, animal goods
        return product.category in ("ex", "t") or product.category == farm_cat
    return product.category in ("v", "t", "other") or product.category == farm_cat


class Vehicle:
    """Represents a logistics transport vehicle operating on a specific route."""

    def __init__(
        self,
        client: MFFGameClient,
        state: VehicleState,
        config: VehicleConfigData,
        route_config: VehicleRouteConfig,
        stock_service: StockService | None = None,
        sushibar_service: Any | None = None,
    ):
        self.client = client
        self.state = state
        self.config = config
        self.route_config = route_config
        self.stock_service = stock_service
        self.sushibar_service = sushibar_service
        self.last_sent_cart: str = ""
        self.staged_cart: str = ""
        self.current_cargo: list[VehicleCargoItem] = []

    @property
    def current_location(self) -> int:
        return self.state.current

    @property
    def route_id(self) -> int:
        return self.state.route

    @property
    def vehicle_id(self) -> int:
        return self.state.vehicle_type

    @property
    def remain_seconds(self) -> int:
        return max(0, self.state.remain)

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def capacity(self) -> int:
        return self.config.capacity

    @property
    def products_slots(self) -> int:
        return self.config.products

    @property
    def target_farm_id(self) -> int:
        return (
            self.route_config.farm_id
            if self.route_config.farm_id
            else (self.config.farms[0] if self.config.farms else 5)
        )

    def update_state(self, state: VehicleState):
        self.state = state

    def update_config(self, config: VehicleConfigData):
        self.config = config

    def get_status(self) -> str:
        if not self.route_config.transport:
            return "disabled"
        if self.remain_seconds > 0:
            return "driving"
        if self.current_location == 1:
            return "ready_main"
        if self.current_location == self.target_farm_id:
            return "ready_outer"
        return "idle"

    async def loop(
        self,
        stock_service: StockService,
        quest_requirements: dict[int, int] | None = None,
        quest_priority_order: dict[int, int] | None = None,
        sushibar_service: Any | None = None,
    ) -> bool:
        """Execute one logistics check for this vehicle."""
        self.stock_service = stock_service
        if sushibar_service is not None:
            self.sushibar_service = sushibar_service

        if not self.route_config.transport:
            return False

        # In transit cooldown
        if self.remain_seconds > 0:
            logger.debug(
                f"Fahrzeug '{self.name}' auf Route {self.route_id} unterwegs "
                f"(Restzeit: {self.remain_seconds}s)."
            )
            return False

        # Leg 1: At Farm 1 (Main farm) -> Send to outer farm
        if self.current_location == 1:
            return await self._handle_main_to_outer(
                stock_service,
                quest_requirements=quest_requirements,
                sushibar_service=sushibar_service or self.sushibar_service,
            )

        # Leg 2: At Outer farm -> Send to Farm 1
        if self.current_location == self.target_farm_id:
            return await self._handle_outer_to_main(
                stock_service,
                quest_requirements=quest_requirements,
                quest_priority_order=quest_priority_order,
                sushibar_service=sushibar_service or self.sushibar_service,
            )

        return False

    async def _handle_main_to_outer(
        self,
        stock_service: StockService,
        quest_requirements: dict[int, int] | None = None,
        sushibar_service: Any | None = None,
    ) -> bool:
        """Handle supplies dispatch from Farm 1 to outer farm."""
        cart = ""
        self.current_cargo = []

        products_to_send: list[int] = []

        # 1. Standard configured required products (supply stops at 4000)
        for req in self.route_config.required_products:
            pid = resolve_pid(req, stock_service)
            if pid is None:
                continue
            outer_stock = max(
                stock_service.get_farm_amount(self.target_farm_id, pid),
                stock_service.get_temp_amount(self.target_farm_id, pid),
            )
            if outer_stock < 4000:
                products_to_send.append(pid)

        # 2. Sushi-Bar ingredients (Farm 8) - only supply when reserve < threshold (500)
        if self.route_config.sushi_supply:
            sushi_ingredients = get_sushibar_needed_ingredients(
                sushibar_service or self.sushibar_service,
                stock_service=stock_service,
            )
            deficient_sushi: list[tuple[int, int]] = []
            farm_cat = settings.agriculture.get_farm_category(self.target_farm_id)
            for s_pid in sushi_ingredients:
                p_obj = stock_service.get_product(s_pid)
                # Exclude water plants locally grown on Farm 8
                if p_obj and (p_obj.category == "water" or p_obj.category == farm_cat):
                    continue
                outer_stock = stock_service.get_temp_amount(self.target_farm_id, s_pid)
                if outer_stock < self.route_config.sushi_reserve_threshold:
                    deficient_sushi.append((outer_stock, s_pid))

            # Prioritize by lowest stock on outer farm first
            deficient_sushi.sort(key=lambda x: x[0])
            for _, s_pid in deficient_sushi:
                if s_pid not in products_to_send:
                    products_to_send.append(s_pid)

        if products_to_send:
            slots_to_use = min(len(products_to_send), self.products_slots)
            load_per_slot = self.capacity // slots_to_use
            slot = 1

            for pid in products_to_send:
                if slot > self.products_slots or load_per_slot <= 0:
                    break

                # Ensure stock is available on Farm 1 (grasps/buys if needed)
                await stock_service.grasp_products([{"pid": pid, "amount": load_per_slot}])
                avail = stock_service.get_amount(pid)
                actual_load = min(load_per_slot, avail)

                if actual_load > 0:
                    cart += f"{slot},{pid},{actual_load}_"
                    stock_service.deduct_stock(pid, actual_load, farm_id=1)
                    p_obj = stock_service.get_product(pid)
                    p_name = p_obj.name if p_obj else f"PID {pid}"
                    self.current_cargo.append(
                        VehicleCargoItem(
                            slot=slot,
                            pid=pid,
                            name=p_name,
                            amount=actual_load,
                            is_quest_product=False,
                        )
                    )
                    slot += 1

        # Check whether outer farm has demand if cart is empty
        has_outer_demand = False
        if cart != "":
            has_outer_demand = True
        elif self.route_config.transport:
            if self.route_config.only_quest_products:
                # Only travel empty if outer farm has quest goods ready to collect
                farm_category = settings.agriculture.get_farm_category(self.target_farm_id)
                if quest_requirements:
                    for p in stock_service.products.values():
                        if not is_product_for_farm(p, self.target_farm_id):
                            continue
                        if p.pid in quest_requirements:
                            target_demand = quest_requirements[p.pid]
                            if stock_service.get_amount(p.pid) < target_demand:
                                outer_amt = stock_service.get_temp_amount(self.target_farm_id, p.pid)
                                min_reserve = 0
                                if p.category == farm_category or p.category == "water":
                                    if p.size_x and p.size_y:
                                        min_reserve = 120 // (p.size_x * p.size_y)
                                    if self.route_config.min_crop_reserve > 0:
                                        min_reserve = max(min_reserve, self.route_config.min_crop_reserve)
                                if outer_amt > min_reserve:
                                    has_outer_demand = True
                                    break
            else:
                has_outer_demand = True

        if cart != "" or (self.route_config.transport and has_outer_demand):
            return await self.send_vehicle(cart)

        logger.info(
            f"Fahrzeug '{self.name}' wartet auf Farm 1 (Kein Versorgungs- oder Quest-Bedarf für Farm {self.target_farm_id})."
        )
        return False

    async def _handle_outer_to_main(
        self,
        stock_service: StockService,
        quest_requirements: dict[int, int] | None = None,
        quest_priority_order: dict[int, int] | None = None,
        sushibar_service: Any | None = None,
    ) -> bool:
        """Handle loading harvested crops/products on outer farm and returning to Farm 1."""
        if not self.route_config.transport:
            logger.debug(f"Transport für Farm {self.target_farm_id} deaktiviert.")
            return False

        farm_category = settings.agriculture.get_farm_category(self.target_farm_id)
        is_farm_10 = self.target_farm_id == 10 or self.route_config.only_milled_surplus

        # Required supply products must not be shipped back as harvest surplus
        required_pids = {
            resolve_pid(req, stock_service)
            for req in self.route_config.required_products
        } - {None}
        if self.route_config.sushi_supply:
            required_pids.update(
                get_sushibar_needed_ingredients(
                    sushibar_service or self.sushibar_service,
                    stock_service=stock_service,
                )
            )

        # Locally produced farm goods are harvest items, never incoming supplies
        required_pids = {
            pid
            for pid in required_pids
            if not is_product_for_farm(
                stock_service.get_product(pid) or Product(pid=pid, name=""),
                self.target_farm_id,
            )
        }

        # Collect available products on outer farm rack (tempstock)
        available_products: list[Product] = []
        for p in stock_service.products.values():
            if not is_product_for_farm(p, self.target_farm_id):
                continue
            if p.pid in required_pids:
                continue
            outer_amt = stock_service.get_temp_amount(self.target_farm_id, p.pid)
            if outer_amt <= 0:
                continue

            is_quest = bool(
                self.route_config.prioritize_quests
                and quest_requirements
                and p.pid in quest_requirements
                and stock_service.get_amount(p.pid) < quest_requirements[p.pid]
            )

            # Rule for only_quest_products (Farm 8): ONLY load products demanded by active quests
            if self.route_config.only_quest_products:
                if not is_quest:
                    continue
            elif is_farm_10:
                is_milled = "gemahlen" in p.name.lower()
                if not is_milled and not (self.route_config.prioritize_quests and is_quest):
                    continue
            elif p.category != farm_category and not is_quest:
                continue

            available_products.append(p)

        # Seed reserve & safety reserve check: e.g. 500 safety reserve for water plants
        def get_surplus(p: Product) -> int:
            current_amt = stock_service.get_temp_amount(self.target_farm_id, p.pid)
            min_reserve = 0
            if p.category == farm_category or p.category == "water":
                if p.size_x and p.size_y:
                    min_reserve = 120 // (p.size_x * p.size_y)
                if self.route_config.min_crop_reserve > 0:
                    min_reserve = max(min_reserve, self.route_config.min_crop_reserve)
            return max(0, current_amt - min_reserve)

        # Split into Priority 1 (Quest-Bedarfe) and Priority 2 (Ernteüberschuss)
        prio_1_quests: list[Product] = []
        prio_2_surplus: list[Product] = []

        for p in available_products:
            surplus = get_surplus(p)
            if surplus <= 0:
                continue

            needed_for_quest = False
            if self.route_config.prioritize_quests and quest_requirements and p.pid in quest_requirements:
                target_demand = quest_requirements[p.pid]
                current_main_stock = stock_service.get_amount(p.pid)
                if current_main_stock < target_demand:
                    needed_for_quest = True

            if needed_for_quest:
                prio_1_quests.append(p)
            elif not self.route_config.only_quest_products:
                # On Farm 10, surplus must be milled
                if is_farm_10 and "gemahlen" not in p.name.lower():
                    continue
                prio_2_surplus.append(p)

        # Sort Prio 1 by quest priority order (earliest quest first), then by quest deficit descending
        if quest_requirements:
            prio_order = quest_priority_order or {}
            prio_1_quests.sort(
                key=lambda p: (
                    prio_order.get(p.pid, 999999),
                    -(quest_requirements.get(p.pid, 0) - stock_service.get_amount(p.pid)),
                ),
            )

        # Sort Prio 2 by surplus amount descending
        prio_2_surplus.sort(key=get_surplus, reverse=True)

        ordered_candidates = prio_1_quests + prio_2_surplus

        remaining_capacity = self.capacity
        cart = ""
        slot = 1
        self.current_cargo = []
        quest_satisfied = False

        for plant in ordered_candidates:
            surplus = get_surplus(plant)
            if surplus <= 0:
                continue

            load = min(remaining_capacity, surplus)
            if load > 0:
                cart += f"{slot},{plant.pid},{load}_"
                remaining_capacity -= load

                is_qp = bool(quest_requirements and plant.pid in quest_requirements)
                if is_qp and quest_requirements:
                    deficit = quest_requirements[plant.pid] - stock_service.get_amount(plant.pid)
                    if deficit > 0 and load >= deficit:
                        quest_satisfied = True

                self.current_cargo.append(
                    VehicleCargoItem(
                        slot=slot,
                        pid=plant.pid,
                        name=plant.name,
                        amount=load,
                        is_quest_product=is_qp,
                    )
                )
                slot += 1

            if remaining_capacity <= 0 or slot > self.products_slots:
                break

        # Check departure conditions:
        is_full = remaining_capacity == 0
        supplies_urgent = False
        threshold = self.route_config.sushi_reserve_threshold if self.route_config.sushi_supply else 500
        for pid in required_pids:
            if pid is not None:
                outer_stock = max(
                    stock_service.get_farm_amount(self.target_farm_id, pid),
                    stock_service.get_temp_amount(self.target_farm_id, pid),
                )
                if outer_stock < threshold:
                    supplies_urgent = True
                    break

        quest_trigger = cart != "" and quest_satisfied
        partial_trigger = cart != "" and self.route_config.send_partial

        if is_full or supplies_urgent or quest_trigger or partial_trigger:
            for item in self.current_cargo:
                stock_service.deduct_temp_stock(item.pid, item.amount, farm_id=self.target_farm_id)
            self.staged_cart = ""
            return await self.send_vehicle(cart)

        self.staged_cart = cart
        logger.info(
            f"Fahrzeug '{self.name}' wartet auf Farm {self.target_farm_id} auf weitere Ernte / Quest-Bedarfe "
            f"(Ladung: {self.capacity - remaining_capacity}/{self.capacity}, Dringend: {supplies_urgent})."
        )
        return False

    async def send_vehicle(self, cart: str = "") -> bool:
        """Dispatch the vehicle via farm.php?mode=map_sendvehicle."""
        dest_farm = self.target_farm_id if self.current_location == 1 else 1
        logger.info(
            f"Fahre {self.name} (Route {self.route_id}, Typ {self.vehicle_id}) "
            f"von Farm {self.current_location} nach Farm {dest_farm} "
            f"mit Fracht '{cart}'..."
        )

        res = await self.client.api_call(
            "farm",
            {
                "mode": "map_sendvehicle",
                "farm": self.current_location,
                "position": 1,
                "route": self.route_id,
                "vehicle": self.vehicle_id,
                "cart": cart,
            },
        )

        datablock = res.get("datablock")
        success = datablock == 1 or datablock == [1] or (isinstance(datablock, list) and 1 in datablock)

        if success:
            logger.info(f"Fahrzeug '{self.name}' erfolgreich losgeschickt.")
            self.last_sent_cart = cart

            # Update state if returned in updateblock
            updateblock = res.get("updateblock", {})
            if self.stock_service and updateblock:
                self.stock_service.update(res)

            map_data = updateblock.get("map", {})
            vehicles_dict = map_data.get("vehicles", {})
            route_str = str(self.route_id)
            v_id_str = str(self.vehicle_id)

            if route_str in vehicles_dict and v_id_str in vehicles_dict[route_str]:
                v_state_raw = vehicles_dict[route_str][v_id_str]
                self.state = VehicleState(
                    current=int(v_state_raw.get("current", dest_farm)),
                    route=int(v_state_raw.get("route", self.route_id)),
                    type=int(v_state_raw.get("type", self.vehicle_id)),
                    remain=int(v_state_raw.get("remain", self.config.duration or 3600)),
                )
            else:
                # Fallback estimation
                self.state.current = dest_farm
                self.state.remain = self.config.duration or 3600

            return True

        logger.warning(f"Senden von Fahrzeug '{self.name}' fehlgeschlagen: {res}")
        return False

    def to_info(self) -> VehicleInfo:
        return VehicleInfo(
            route_id=self.route_id,
            vehicle_id=self.vehicle_id,
            target_farm_id=self.target_farm_id,
            name=self.name,
            capacity=self.capacity,
            products_slots=self.products_slots,
            duration=self.config.duration,
            current_location=self.current_location,
            remain_seconds=self.remain_seconds,
            status=self.get_status(),
            transport_enabled=self.route_config.transport,
            required_products=self.route_config.required_products,
            cargo=self.current_cargo,
            last_sent_cart=self.last_sent_cart,
            only_quest_products=self.route_config.only_quest_products,
            sushi_supply=self.route_config.sushi_supply,
        )


class VehicleService:
    """Coordinates logistics transport vehicles across all configured routes."""

    def __init__(
        self,
        client: MFFGameClient,
        config: VehiclesConfig | None = None,
    ):
        self.client = client
        self.config = config or settings.vehicles
        self.vehicles: dict[int, Vehicle] = {}

    def update(self, map_data: dict[str, Any] | None):
        """Parse vehicle states and static specifications from updateblock.map."""
        if not map_data or not isinstance(map_data, dict):
            return

        vehicles_map = map_data.get("vehicles", {})
        config_map = map_data.get("config", {}).get("vehicles", {})

        if not isinstance(vehicles_map, dict):
            return

        for route_cfg in self.config.routes.values():
            route_id = route_cfg.route
            route_key_str = str(route_id)

            if route_key_str not in vehicles_map and route_id not in vehicles_map:
                continue

            available_vehicles = vehicles_map.get(route_key_str) or vehicles_map.get(route_id)
            if not isinstance(available_vehicles, dict) or not available_vehicles:
                continue

            # Determine best vehicle: fastest or user-specified
            selected_v_id: int | None = None
            if route_cfg.vehicle and not route_cfg.auto_fastest:
                v_str = str(route_cfg.vehicle)
                if v_str in available_vehicles or route_cfg.vehicle in available_vehicles:
                    selected_v_id = route_cfg.vehicle

            if selected_v_id is None:
                # Auto-fastest: scan all available vehicles on this route
                scored_vehicles: list[tuple[float, int]] = []
                for v_id_raw in available_vehicles:
                    if not str(v_id_raw).isdigit():
                        continue
                    v_id = int(v_id_raw)
                    v_cfg_raw = (
                        config_map.get(str(v_id))
                        or config_map.get(v_id)
                        or {}
                    )
                    v_cfg = VehicleConfigData(
                        name=str(v_cfg_raw.get("name", f"Fahrzeug {v_id}")),
                        capacity=int(v_cfg_raw.get("capacity", 500)),
                        products=int(v_cfg_raw.get("products", 2)),
                        farms=[int(f) for f in v_cfg_raw.get("farms", [route_cfg.farm_id])],
                        duration=int(v_cfg_raw.get("duration", 0)),
                        speed=float(v_cfg_raw.get("speed", 1.0)),
                    )
                    score = calculate_speed_score(v_cfg, v_id)
                    scored_vehicles.append((score, v_id))

                if scored_vehicles:
                    scored_vehicles.sort(key=lambda x: x[0], reverse=True)
                    selected_v_id = scored_vehicles[0][1]

            if selected_v_id is None:
                continue

            v_state_raw = available_vehicles.get(str(selected_v_id)) or available_vehicles.get(selected_v_id)
            if not isinstance(v_state_raw, dict):
                continue

            state = VehicleState(
                current=int(v_state_raw.get("current", 1)),
                route=int(v_state_raw.get("route", route_id)),
                type=int(v_state_raw.get("type", selected_v_id)),
                remain=int(v_state_raw.get("remain", 0)),
            )

            v_cfg_raw = config_map.get(str(selected_v_id)) or config_map.get(selected_v_id) or {}
            config = VehicleConfigData(
                name=str(v_cfg_raw.get("name", f"Fahrzeug {selected_v_id}")),
                capacity=int(v_cfg_raw.get("capacity", 500)),
                products=int(v_cfg_raw.get("products", 2)),
                farms=[int(f) for f in v_cfg_raw.get("farms", [route_cfg.farm_id])],
                duration=int(v_cfg_raw.get("duration", 0)),
                speed=float(v_cfg_raw.get("speed", 1.0)),
            )

            if route_id in self.vehicles:
                self.vehicles[route_id].update_state(state)
                self.vehicles[route_id].update_config(config)
            else:
                self.vehicles[route_id] = Vehicle(
                    client=self.client,
                    state=state,
                    config=config,
                    route_config=route_cfg,
                )

        logger.info(f"VehicleService: {len(self.vehicles)} aktive Route(n) initialisiert.")

    async def loop(
        self,
        stock_service: StockService,
        quest_requirements: dict[int, int] | None = None,
        quest_priority_order: dict[int, int] | None = None,
        sushibar_service: Any | None = None,
    ):
        """Execute one serving loop across all vehicles."""
        if not self.config.enabled:
            return

        logger.info("========== VehicleService: Starte Logistik-Zyklus ==========")
        for route_id, vehicle in self.vehicles.items():
            try:
                await vehicle.loop(
                    stock_service,
                    quest_requirements=quest_requirements,
                    quest_priority_order=quest_priority_order,
                    sushibar_service=sushibar_service,
                )
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(
                    f"VehicleService: Fehler bei Fahrzeug auf Route {route_id}: {e}"
                )
        logger.info("========== VehicleService: Logistik-Zyklus beendet ==========")

    def get_vehicle_infos(self) -> list[VehicleInfo]:
        """Return list of DTOs for API and Dashboard."""
        return [v.to_info() for v in self.vehicles.values()]

    async def send_manual(self, route_id: int, cart: str | None = None) -> bool:
        """Manually trigger sending a vehicle on a given route."""
        vehicle = self.vehicles.get(route_id)
        if not vehicle:
            logger.warning(f"VehicleService: Route {route_id} nicht gefunden.")
            return False
        cart_to_send = cart if (cart is not None and cart != "") else getattr(vehicle, "staged_cart", "")
        if (
            cart_to_send
            and cart_to_send == getattr(vehicle, "staged_cart", "")
            and vehicle.current_location != 1
        ):
            if vehicle.stock_service:
                for item in vehicle.current_cargo:
                    vehicle.stock_service.deduct_temp_stock(
                        item.pid, item.amount, farm_id=vehicle.target_farm_id
                    )
            vehicle.staged_cart = ""
        return await vehicle.send_vehicle(cart_to_send)
