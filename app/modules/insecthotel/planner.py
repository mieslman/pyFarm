"""Optimization planner and dynamic rotation engine for Insect Hotel feeding."""

from typing import Any

from loguru import logger

from app.modules.insecthotel.models import InsectHotelSnapshot, InsectNicheSlot

# PIDs of the 12 regular farm crops and berries consumed by hotel insects
INSECT_PRODUCTS: dict[int, str] = {
    19: "Radieschen",
    20: "Erdbeeren",
    21: "Tomaten",
    22: "Zwiebeln",
    23: "Spinat",
    24: "Blumenkohl",
    26: "Kartoffeln",
    31: "Zucchini",
    32: "Heidelbeeren",
    33: "Himbeeren",
    34: "Johannisbeeren",
    35: "Brombeeren",
}

# Verified species characteristics from upstream game engine
DEFAULT_SPECIES_CONFIG: dict[str, dict[str, Any]] = {
    "1": {
        "name": "Wildbienen",
        "decay": 4,
        "limits": (20.0, 50.0),
        "gain": 4,
        "loss": 2,
        "plants": {19: 1.8, 20: 2.2, 21: 1.4, 34: 0.4},
    },
    "2": {
        "name": "Ohrwürmer",
        "decay": 3,
        "limits": (25.0, 50.0),
        "gain": 5,
        "loss": 4,
        "plants": {20: 1.2, 22: 1.5, 23: 1.05, 26: 0.3},
    },
    "3": {
        "name": "Schwebfliegen",
        "decay": 2,
        "limits": (30.0, 60.0),
        "gain": 12,
        "loss": 11,
        "plants": {19: 1.0, 22: 0.8, 24: 0.7, 32: 0.2},
    },
    "4": {
        "name": "Marienkäfer",
        "decay": 3,
        "limits": (35.0, 60.0),
        "gain": 7,
        "loss": 7,
        "plants": {21: 0.3, 26: 1.5, 31: 1.05, 35: 1.2},
    },
    "5": {
        "name": "Florfliegen",
        "decay": 2,
        "limits": (40.0, 65.0),
        "gain": 15,
        "loss": 15,
        "plants": {23: 0.2, 31: 0.8, 32: 1.0, 33: 0.7},
    },
    "6": {
        "name": "Schmetterling",
        "decay": 5,
        "limits": (50.0, 65.0),
        "gain": 2,
        "loss": 2,
        "plants": {24: 0.5, 33: 2.0, 34: 2.5, 35: 1.75},
    },
}

# The unique 8-plant set allowing all 3 priority species (Schmetterling, Marienkäfer, Schwebfliegen) to grow
TRIPLE_GROWTH_PIDS = [34, 35, 33, 26, 31, 24, 19, 22]

# The rescue & twin-growth set: Erdbeeren (20) replaces Blumenkohl (24) to save Wildbienen (+0.4) and stabilize Ohrwürmer (0.0)
RESCUE_TWIN_GROWTH_PIDS = [34, 35, 33, 26, 31, 20, 19, 22]


def calculate_species_net_happiness(
    species_id: str,
    available_pids: set[int],
    slot: InsectNicheSlot | None = None,
) -> float:
    """Calculate net happiness change (sum of provided happiness minus decay) per 4h cycle."""
    plants: dict[int, float] = {}
    decay = 0

    if slot and slot.plants and slot.happiness_decay > 0:
        plants = slot.plants
        decay = slot.happiness_decay
    else:
        cfg = DEFAULT_SPECIES_CONFIG.get(str(species_id), {})
        plants = cfg.get("plants", {})
        decay = cfg.get("decay", 0)

    gained = sum(h for pid, h in plants.items() if pid in available_pids)
    return round(gained - decay, 3)


def get_endangered_species(
    snapshot: InsectHotelSnapshot,
    min_safety_happiness: float = 25.0,
) -> list[str]:
    """Identify living insect species whose happiness is near or below their population loss limit."""
    endangered = []
    for slot_id, slot in snapshot.slots.items():
        if slot.population <= 0:
            continue

        cfg = DEFAULT_SPECIES_CONFIG.get(str(slot_id), {})
        min_limit = (
            slot.min_happiness if slot.min_happiness > 0 else cfg.get("limits", (25.0, 50.0))[0]
        )
        # Safety buffer of 5.0 above the loss limit
        threshold = max(min_safety_happiness, min_limit + 5.0)

        if slot.happiness <= threshold:
            endangered.append(slot.name or f"Slot {slot_id}")

    return endangered


def plan_target_pids(
    snapshot: InsectHotelSnapshot,
    max_slots: int = 8,
    strategy: str = "dynamic_rotation",
    priority_species: list[str] | None = None,
    min_safety_happiness: float = 25.0,
) -> list[int]:
    """Compute the optimal ordered list of target crop PIDs for the hotel's food slots.

    Strategies:
    - 'dynamic_rotation': Dynamically rotates between Triple Growth (pushing Schmetterling, Marienkäfer,
                          Schwebfliege) and Rescue/Twin Growth (safeguarding Wildbienen and Ohrwürmer with Erdbeeren).
    - 'prio_growth': Strictly focuses on the 3 priority species (Blumenkohl included).
    - 'balanced': Strictly uses the balanced set (Erdbeeren included, Bienen/Ohrwürmer safe).
    - 'fixed': Keeps existing slot assignments unchanged.
    """
    if strategy == "fixed":
        current = [s.pid for s in snapshot.stock_slots.values() if s.pid]
        return current[:max_slots]

    if strategy == "prio_growth":
        return TRIPLE_GROWTH_PIDS[:max_slots]

    if strategy == "balanced":
        return RESCUE_TWIN_GROWTH_PIDS[:max_slots]

    # Strategy: dynamic_rotation
    # 1. Check if Wildbienen (slot 1) or Ohrwürmer (slot 2) need protection
    bienen_slot = snapshot.slots.get("1")
    ohrwurm_slot = snapshot.slots.get("2")

    bienen_endangered = False
    if bienen_slot and bienen_slot.population > 0:
        limit = bienen_slot.min_happiness if bienen_slot.min_happiness > 0 else 20.0
        # If Wildbienen happiness is below threshold (e.g. 25.0 or limit + 5)
        if bienen_slot.happiness <= max(min_safety_happiness, limit + 5.0):
            bienen_endangered = True

    ohrwurm_endangered = False
    if ohrwurm_slot and ohrwurm_slot.population > 0:
        limit = ohrwurm_slot.min_happiness if ohrwurm_slot.min_happiness > 0 else 25.0
        if ohrwurm_slot.happiness <= max(min_safety_happiness, limit + 5.0):
            ohrwurm_endangered = True

    florfliegen_slot = snapshot.slots.get("5")
    florfliegen_endangered = False
    if florfliegen_slot and florfliegen_slot.population > 0:
        limit = florfliegen_slot.min_happiness if florfliegen_slot.min_happiness > 0 else 40.0
        if florfliegen_slot.happiness <= limit + 5.0:
            florfliegen_endangered = True

    if bienen_endangered or ohrwurm_endangered:
        logger.info(
            f"InsectHotelPlanner: Schutzmodus aktiv (Wildbienen gefährdet: {bienen_endangered}, "
            f"Ohrwürmer gefährdet: {ohrwurm_endangered}). Verwende Rettungsset mit Erdbeeren (PID 20)."
        )
        target = list(RESCUE_TWIN_GROWTH_PIDS)
    else:
        logger.info(
            "InsectHotelPlanner: Bestände gesichert. Verwende Wachstumsset mit Blumenkohl (PID 24) "
            "für maximales Wachstum von Schmetterlingen (+1.75), Marienkäfern (+0.75) und Schwebfliegen (+0.50)."
        )
        target = list(TRIPLE_GROWTH_PIDS)

    # If Florfliegen are critically low, inject Heidelbeeren (PID 32)
    if florfliegen_endangered and 32 not in target:
        # Replace least critical crop (e.g. Radieschen 19 or Zwiebeln 22) with Heidelbeeren (32)
        if 22 in target:
            idx = target.index(22)
            target[idx] = 32
        elif 19 in target:
            idx = target.index(19)
            target[idx] = 32

    return target[:max_slots]
