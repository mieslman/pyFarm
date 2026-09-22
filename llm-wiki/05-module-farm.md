---
title: Modul Farm (Hauptfarm & Gebäude) - Source-Code & Schnittstellen
author: System
date: 2026-09-11
type: module
description: Detaillierte Implementierung der Kern-Farm-Verwaltung, Fabriken und Tierställe.
tags: [module, farm, buildings, factory, shed, interfaces]
---
# Modul: Farm (Hauptfarm & Gebäude)

Dieses Modul verwaltet die Gebäude der Hauptfarmen: Fabriken (Weiterverarbeitung) und Ställe/Sheds (Tierhaltung).

## 1. Klassen- & Dateiübersicht

### `FarmService.js` (Orchestrierung)
- **Klasse:** `FarmService`
- **Singleton-Export:** `const farmService = new FarmService(); module.exports = farmService;`
- **Methoden:**
  - `async loop()`:
    1. Ruft `updateFarms()` auf.
    2. Iteriert über alle Farmen und Felder: Ruft für vorhandene Gebäude `field.building.serve()` auf.
  - `async updateFarms()`:
    - Holt Daten: `RestApi.apiCall('farm', {mode:'getfarms', farm:1, position:0})`.
    - Mappt `buildingid` via `getBuildingConfig(config)`:
      - ID `1` -> `Field`
      - `advancedbuilding` oder IDs `13, 14, 16` -> `Factory`
      - `building2product` -> `Shed` (Tierstall)

### `Building.js` (Basisklasse)
- **Klasse:** `Building`
- **Getter:** `farm`, `position`, `buildingId`, `name`.

### `Factory.js` (Altsystem) & Python `Factory` (`app/modules/farm_buildings/factory.py`) ✅
- **Altsystem:** `farm/Factory.js` für `advancedbuilding` sowie IDs 13, 14, 16.
- **Python-Redesign (Vollständig implementiert & live verifiziert):**
  - **Klasse:** `Factory` (`app/modules/farm_buildings/factory.py`), Modelle in `app/models/factory.py`.
  - **Erkannte Gebäude (Server 21):**
    - Farm 2: Käserei (Pos 1 & 4, ID 8), Ölpresse (Pos 5, ID 13).
    - Farm 5: Wollspinnerei (Pos 5, ID 9), Strickerei (Pos 6, ID 16).
    - Farm 9: Marmeladenküche (Pos 1 & 2, ID 25).
  - **Ernte:** `mode: 'harvestproduction', farm, position, slot` bei fertigen Slots (`ready: 1`).
  - **Produktions-Priorisierung:**
    1. **Vorrang für Quests:** Produkte mit Fehlmengen (`missing > 0`) in aktiven Quests haben höchste Priorität.
    2. **Geringster Lagerbestand:** Liegt kein Quest-Bedarf vor, wird das herstellbare Produkt mit dem niedrigsten Lagerbestand gewählt.
    3. **Strikte Farm-5-Isolierung:** Für Betriebe auf Farmen $\ge 5$ (Farm 5 Spinnerei & Strickerei, Farm 9 Marmeladenküche) dürfen Zutaten ausschließlich aus dem lokalen Regal dieser Farm entnommen werden (`stock_service.get_farm_amount(farm_id, pid, include_fallback=False)`).
    4. **Strikter Coin-Schutz:** Slots mit `block: 1` (wie Slot 3 der Ölpresse für Coin-Miete) werden niemals angetastet.
  - **Produktionsstart:** `mode: 'start', farm, position, slot, item: recipe.item_id`.
  - **REST-API & Dashboard:** Endpunkte unter `/api/v1/factories`, interaktiver Tab **Fabriken** im Web-Dashboard.


### `Shed.js` (Tierställe: Hühner, Kühe, Schafe, Bienen)
- **Klasse:** `Shed extends Building`
- **Methoden:**
  - `async serve()`: `update()` -> `crop()` -> `feed()`.
  - `async update()`: API-Call `{mode: 'inner_init', farm, position}`. Parst `barn` Daten.
  - `async crop()`: Wenn Erzeugnisse fertig sind (`barn.remain === 0`), API-Call `{mode: 'inner_crop', farm, position}`.
  - `async feed()`:
    - Sucht die **günstigste Futterart** für das Tier basierend auf Marktpreisen (`barn.feed[pid] * Stock.products[pid].price`).
    - Füttert Tiere: API-Call `{mode: 'inner_feed', farm, position, pid, amount}`.

### `Fuelstation.js` / Python `Fuelstation` (Biosprit-Anlage, Building ID 20)
- **Klasse:** `Fuelstation` (in Python: `app/modules/farm_buildings/fuelstation.py`, Models: `app/models/fuelstation.py`)
- **Produktionspunkte vs. Level-Punkte (Wichtige Unterscheidung):**
  - Jeder Slot besitzt ein Level (1 bis 13). Das zum Starten einer Produktion erforderliche Punktelimit ist in `constants.slot_level[str(level)].limit` definiert (z. B. Level 1: 1.000.000, Level 5: 5.000.000 Punkte).
  - Das Attribut `points_left` in den Slot-Stammdaten des Servers bezeichnet lediglich die für den nächsten **Slot-Stufenaufstieg** (Level-Up) noch fehlenden Punkte (`level_points_next - count`). Es darf **nicht** als Produktionsbedarf missverstanden werden.
  - Der aktuelle Füllstand eines Slots ergibt sich aus der Summe aller bereits eingeworfenen Waren:
    $$\text{current\_points} = \sum_{\text{pid}} \text{points}(\text{pid}) \cdot \text{entries}[\text{pid}]$$
  - Der noch benötigte Bedarf zum Starten der Produktion ist:
    $$\text{points\_needed} = \max(0, \text{limit} - \text{current\_points})$$
- **Methoden:**
  - `async serve(stock_service)`:
    1. Erntet fertige Slots (`slot.is_finished`, `remain <= 0`): API-Call `{mode: 'fuelstation_harvest', farm, position, slot}`.
    2. Befüllt unfertige/freie Slots (`slot.is_waiting_for_refill`), sofern nicht blockiert (`slot.is_blocked` mit Coins):
       - Iteriert durch bevorzugte Rohstoffe (z. B. Mais PID 2, Gurken PID 18, Karotten PID 17, Getreide PID 1) mit Bestand oberhalb `min_reserve`.
       - Berechnet für jeden Kandidaten die benötigte Menge `ceil(slot.points_needed / pts_per_unit)` und wirft diese ein via:
         `API-Call {mode: 'fuelstation_entry', farm, position, slot, pid, amount}`.
       - Reicht ein einzelner Rohstoff nicht aus, bricht die Schleife nicht ab, sondern befüllt mit dem nächsten Kandidaten weiter, bis `points_needed <= 0` erreicht ist und der Slot anläuft.

---

## 2. Externe Spielserver-Schnittstellen (MFF AJAX)

| Service | Mode | Parameter | Zweck |
| :--- | :--- | :--- | :--- |
| `farm` | `getfarms` | `farm=1, position=0` | Gesamtübersicht aller Farmen & Gebäude |
| `farm` | `inner_init` | `farm, position` | Gebäude-/Stallansicht öffnen |
| `farm` | `inner_crop` | `farm, position, [slot]` | Tierprodukte oder Fabrikprodukte ernten |
| `farm` | `inner_feed` | `farm, position, pid, amount` | Tiere mit Futter versorgen |
| `farm` | `inner_init_production` | `farm, position, pid, amount, slot` | Produktionsauftrag in Fabrik starten |
| `farm` | `fuelstation_harvest` | `farm, position, slot` | Biosprit-Marken aus fertigem Slot ernten |
| `farm` | `fuelstation_entry` | `farm, position, slot, pid, amount` | Pflanzen/Rohstoffe in Biosprit-Slot einfüllen |

---

## 3. Python-Redesign Empfehlungen

- **Polymorphie mit Pydantic & SQLAlchemy:**
  ```python
  class BuildingBase(BaseModel):
      farm_id: int
      position: int
      building_id: int
      name: str

  class ShedBuilding(BuildingBase):
      animal_type: str
      remain_seconds: Optional[int]
      current_production_pid: Optional[int]

  class FactoryBuilding(BuildingBase):
      slots: dict[int, FactorySlot]
  ```
- **Wirtschaftlichkeits-Engine:** Die Berechnung des günstigsten Futters in `Shed.feed()` ist eine ideale Business-Logik für ein `FeedOptimizer`-Modul in Python.

---

## 4. Verwandte Dokumente

- [**19-python-factories-module.md**](19-python-factories-module.md): Vollständige Spezifikation der Veredelungsfabriken (Ölpresse, Käserei, Wollspinnerei, Strickerei, Marmeladenküche).
- [**00-README.md**](00-README.md): Wiki-Inhaltsverzeichnis.

