---
title: Modul Agriculture - Source-Code & Schnittstellen
author: System
date: 2026-09-11
type: module
description: Detaillierte Implementierung, Klassenstrukturen und API-Schnittstellen des Moduls Agriculture.
tags: [module, agriculture, field, sushibar, vehicle, interfaces]
---
# Modul: Agriculture

Das `agriculture`-Modul steuert den Ackerbau, die Sushibar-Produktion sowie den Warentransport per Fahrzeug zwischen den Farmen.

## 1. Klassen- & Dateiübersicht

### `Agriculture.js` (Controller & Entry Point)
- **Klasse:** `Agriculture`
- **Exportiert:** `async function handleAgriculture()`
- **Konfiguration:** `new Conf({configName: 'Agriculture'})`
- **Methoden:**
  - `async loop()`: Lädt Konfiguration, ruft `update()` auf und iteriert über alle konfigurierten Farmen (`farm.loop()`).
  - `async update(body)`:
    - Ruft `RestApi.apiCall('farm', {mode:'getfarms', farm:1, position:0})` auf.
    - Synchronisiert `Stock.update(body)`.
    - Initialisiert für jede konfigurierte Farm (`farmId`) eine Instanz von `Farm`.

### `Farm.js` (Farm Manager)
- **Klasse:** `Farm`
- **Konstruktor:** `constructor(config, fields, quests, vehicles)`
  - Filtert Gebäude: `buildingid === '1'` -> `new Field(fieldData)`, `buildingid === '23'` -> `new SushiBar(...)`.
  - Initialisiert Fahrzeug: `new Vehicle(...)`.
- **Methoden:**
  - `async loop()`:
    1. Wählt Pflanzliste basierend auf konfigurierter Strategie (`this[this.config.plantStrategy](...)`).
    2. Führt `field.loop(plant)` für alle Felder aus.
    3. Führt `factory.loop()` für alle Sushi-Bars aus.
    4. Wenn Fahrzeug vorhanden und `config.transport === true`: Ruft `vehicle.loop(...)` auf.
  - **Pflanzstrategien:**
    - `plantQuest(strategy)`: Priorisiert Pflanzen basierend auf Quest-Anforderungen (`quests.data[1][0]`).
    - `plantMin(strategy)`: Sortiert Pflanzen aufsteigend nach Lagerbestand (`amount + tmpAmount`).
    - `plantOrders(strategy)` / `plantOrders_1(strategy)`: Filtert nach in Strategie hinterlegten PIDs und sortiert nach Bestand.

### `Field.js` (Acker-Feld Logik)
- **Klasse:** `Field`
- **Eigenschaften:** `farm`, `position`, `name`, `plants` (Array der Slots 1-120).
- **Methoden:**
  - `async loop(plant)`: `update()` -> `crop()` -> `plant(plant)` -> `water()`.
  - `async update(body)`: `RestApi.apiCall('farm', {mode:'gardeninit', farm: this.farm, position: this.position})`.
  - `async crop()`: Prüft `plant.phase === 4` (Reif). API-Call: `{mode: 'cropgarden', farm, position}`.
  - `async plant(plant)`: Wenn weniger als 120 Pflanzen vorhanden. API-Call: `{mode: 'autoplant', farm, position, id: plant.pid, product: plant.pid}`.
  - `async water()`: Filtert Pflanzen mit `!plant.iswater`. API-Call: `{mode: 'watergarden', farm, position}`.

### `SushiBar.js` (Spezialfabrik SushiBar - Altsystem) & Python-Modul `app/modules/sushibar/` ✅
- **Altsystem:** Node.js-Klasse `SushiBar` mit einfacher Schleife (`update`, `harvest`, `produce`, `fillTrain`, `collectFarmi`).
- **Python-Redesign (Vollständig implementiert):**
  - Eigenständiges Modul [`app/modules/sushibar/`](../myfreefarm_python/app/modules/sushibar/) mit Pydantic v2 Datenmodellen.
  - Ausrichtung an **Hauptquestreihe 5 (Wasserschutz)** via `SushiQuestSolver` (ab Quest 64 bis 100).
  - Strikter **Coin-Schutz** (keine Coin-Rezepte, keine bezahlten Slot-Käufe oder Speedups).
  - **Feld-Bepflanzungsreserve:** Mindestens 120 Einheiten (bzw. $120 / (x \times y)$) bleiben für den Wasser-Ackerbau unangetastet.
  - **Cross-Modul Verknüpfung:** Die Wasser-Äcker auf Farm 8 nutzen den Quest-5-Bedarf für die gezielte Aussaat (`plantQuest`).
  - REST-API (`/api/v1/sushibar`) und interaktiver Dashboard-Tab im Webinterface.
  - Vollständige Spezifikation siehe [**18-python-sushibar-module.md**](18-python-sushibar-module.md).


### `Vehicle.js` (Altsystem) & Python-Modul `app/modules/agriculture/vehicle.py` ✅
- **Altsystem:** Node.js-Klasse `Vehicle` mit einfacher Schleife (`loop(plants, requirements)` und `sendVehicle(cart)`).
- **Python-Redesign (Vollständig implementiert):**
  - Eigenständige Klassen `Vehicle` und `VehicleService` in [`app/modules/agriculture/vehicle.py`](../myfreefarm_python/app/modules/agriculture/vehicle.py).
  - Pydantic v2 DTOs in [`app/models/vehicle.py`](../myfreefarm_python/app/models/vehicle.py) (`VehicleState`, `VehicleConfigData`, `VehicleRouteConfig`, `VehicleInfo`).
  - **Automatische Wahl des schnellsten Transportmittels (`auto_fastest: True`):**
    - Scanned alle auf der Route freigeschalteten Fahrzeuge (`updateblock.map.vehicles[route_id]`).
    - Ermittelt automatisch das Transportmittel mit der geringsten Fahrzeit (`duration`), höchsten Geschwindigkeit oder höchsten Fahrzeugstufe/Kapazität (z. B. Traktor vor Handwagen, Pickup vor Traktor).
  - **Quest-Priorisierung beim Beladen:**
    - Ernteprodukte mit offenem Fehlbestand für aktive Quests (`quest_requirements[pid] > stock`) werden als Priorität 1 vor sonstigen Überschüssen in die Lade-Slots gepackt.
    - Deckt die geladene Ladung ein offenes Quest-Defizit, fährt das Fahrzeug auch bei Teilladung vorzeitig ab, um Quest-Fortschritt zu beschleunigen.
  - **Spezialregel Farm 10 (Mühlenprodukte):**
    - Auf Farm 10 werden für allgemeinen Ernteüberschuss (Prio 2) ausschließlich gemahlene Erzeugnisse der Gewürzmühle verladen (`"gemahlen" in name.lower()`). Ungemahlene Gewürze (Pfeffer, Zimt etc.) verbleiben auf Farm 10 als Mahlgut.
  - **Saatgut-Schutz:** Auf Außenfarmen (5, 6, 8, 10) wird zwingend eine Saatgutreserve von $120 / (\text{size\_x} \times \text{size\_y})$ Einheiten im lokalen Farm-Regal (`tempstock`) geschützt.
  - **Versorgungs- & Dringlichkeits-Schwellenwerte:**
    - Versorgungs-Stopp: Lieferungen von Farm 1 stoppen, wenn Außenfarm $\ge 4000$ Einheiten vorrätig hat.
    - Dringende Rückfahrt: Fällt ein Versorgungsgut auf der Außenfarm unter 500 Einheiten, fährt das Fahrzeug sofort zurück nach Farm 1 zur Nachbeschaffung.
  - **REST-API & Dashboard:** Endpunkte `/api/v1/vehicles`, `/api/v1/vehicles/{route}/send` und interaktive Statuskarten im Web-Dashboard.
  - Externe MFF API: `farm.php?mode=map_sendvehicle&farm={current}&position=1&route={route}&vehicle={vehicle}&cart={cart}`. Cart-Format: `${slot},${pid},${amount}_`.

---

## 2. Externe Spielserver-Schnittstellen (MFF AJAX)

| Service | Mode / Action | Parameter | Zweck |
| :--- | :--- | :--- | :--- |
| `farm` | `getfarms` | `farm=1, position=0` | Alle Farmen und Gebäudestatus abrufen |
| `farm` | `gardeninit` | `farm, position` | Einzelnes Feld (120 Felder) initialisieren |
| `farm` | `cropgarden` | `farm, position` | Reife Pflanzen ernten |
| `farm` | `autoplant` | `farm, position, id, product` | Feld komplett bepflanzen |
| `farm` | `watergarden` | `farm, position` | Alle Pflanzen auf Feld gießen |
| `farm` | `sushibar_init` | Keine | Sushibar initialisieren |
| `farm` | `sendvehicle` | `farm, vehicle, cart` | Fahrzeug mit Waren losschicken |

---

## 3. Python-Redesign Empfehlungen

1. **Async Engine:** In Python `asyncio` mit `httpx.AsyncClient` nutzen.
2. **Datenmodelle:**
   ```python
   from pydantic import BaseModel
   from typing import Optional, List

   class PlantSlot(BaseModel):
       slot_id: int
       pid: int
       phase: int  # 4 = reif
       remain: int
       iswater: bool

   class FieldModel(BaseModel):
       farm_id: int
       position: int
       name: str
       plants: List[PlantSlot] = []
   ```
3. **Service-Architektur:** Ein `AgricultureService` orchestriert die Ackerflächen asynchron (parallel via `asyncio.gather` für mehrere Farmen möglich).

---

## 4. Farm-Spezifische Produktkategorien & Lokale Farm-Regale (Verbindliche Regel)

Bestimmte Außenfarmen in MyFreeFarm sind thematische Spezialfarmen und akzeptieren auf ihren Äckern ausschließlich bestimmte Produktkategorien:

| Farm | Farm-Typ | Erlaubte Kategorie | Beispiele |
| :--- | :--- | :--- | :--- |
| **Farm 5** | Exoten-Farm | `ex` | Ananas (351), Limette (352), Papaya (354), Banane (356), Mango (359) |
| **Farm 6** | Berg-/Alpen-Farm | `alpin` | Spitzwegerich (700), Salbei (701), Kümmel (703), Enzian (705), Melisse (708) |
| **Farm 8** | Teich-/Wasser-Farm | `water` | Reis (950), Lotos (951), Wasserspinat (952), Taro-Wurzel (953) |
| **Farm 10** | Kräuter-/Gewürz-Farm | `spice` | Pfeffer (1100), Zimt (1101), Muskat (1102), Piment (1105), Sternanis (1106) |
| **Übrige Farmen** (1, 2, 3, 4, etc.) | Standard-Farmen | `v` (Gemüse/Pflanzen) | Getreide (1), Karotte (2), Kornblume (8), Chili (113), etc. |

### 4.1 Server-Architektur: Lokale Farm-Regale (`stock[farm_id]`)

Die MyFreeFarm-Serverarchitektur trennt das Saatgut und die Erntebestände der Spezialfarmen strikt vom Hauptlager:
- Im Server-Response `updateblock.stock.stock` befinden sich geschachtelte Bestands-Wörterbücher pro Farm-ID:
  - `stock['1']`: Zentrales Hauptlager (Gemüse `v`, Tiererzeugnisse, Standardpflanzen).
  - `stock['5']`: Lokales Regal der Farm 5 (Exoten `ex`).
  - `stock['6']`: Lokales Regal der Farm 6 (Alpinpflanzen `alpin`).
  - `stock['8']`: Lokales Regal der Farm 8 (Wasserpflanzen `water`).
  - `stock['10']`: Lokales Regal der Farm 10 (Gewürze `spice`).
- **Autoplant-Restriktion:** Wenn der Spielbefehl `mode=autoplant&farm={farm}&...` auf einer Spezialfarm aufgerufen wird, greift der Server **ausschließlich** auf das lokale Regal `stock[farm]` dieser Farm zu.
- **Server-Fehlermeldung bei 0 Saatgut ("In deinem Acker ist kein Platz mehr."):**
  - Besitzt das lokale Regal 0 Einheiten des ausgewählten Saatguts, antwortet der Server mit dem Fehlertoken:
    `[0, "In deinem Acker ist kein Platz mehr."]`
  - Dieser Text ist serverseitig irreführend formuliert: Er bedeutet **nicht**, dass der Acker belegt ist, sondern dass im lokalen Farm-Regal keine Samen vorhanden sind (`stock[farm][pid] == 0`).
  - `Field.plant()` fängt Rückgaben mit `datablock[0] == 0` sauber ab und loggt eine Debug-Meldung, anstatt eine Fehlermeldung zu provozieren.

### 4.2 Automatisierungs-Logik & Priorisierung:
- **`FarmStrategyConfig.get_farm_category(farm_id)`**: Ermittelt für jede Farm die verbindliche Kategorie (`ex`, `alpin`, `water`, `spice` oder `v`).
- **`StockService.get_farm_amount(farm_id, pid)`**: Prüft gezielt den Bestand im lokalen Regal der Spezialfarm.
- **`PlantStrategySolver.resolve_candidate()`**: 
  - Validiert feste Vorgaben (`farm_crops`): Weicht die Kategorie einer festen Vorgabe von der Farm-Kategorie ab, wird sie mit Warnung ignoriert und stattdessen dynamisch die passende Pflanze der Farm-Kategorie gewählt.
  - Quests (`plantQuest`): Filtert Anforderungen passend zur jeweiligen Farm-Kategorie (z. B. werden Alpin-Quest-Bedarfe auf Farm 6 gewählt).
  - **Lokale Saatgut-Verfügbarkeit auf Spezialfarmen:**
    - Auf Spezialfarmen (5, 6, 8, 10) prüft der Solver vor der Auswahl eines Kandidaten zwingend `stock_service.get_farm_amount(farm_id, pid) > 0`.
    - Ist für ein Quest-Produkt das lokale Regal leer (z. B. 0 Malve auf Farm 6 bei aktiver Naturschutz-Quest), wird das Produkt verworfen und stattdessen die nächste Quest-Anforderung oder die Kategorie-Pflanze mit dem geringsten Bestand gewählt, für die **tatsächlich Saatgut im lokalen Regal liegt** (z. B. Melisse). Dadurch wird verhindert, dass freigeschaltete Ackerflächen brach und unbepflanzt liegen bleiben.

