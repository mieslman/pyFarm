---
title: Modul Forestry - Source-Code & Schnittstellen
author: System
date: 2026-09-11
type: module
description: Detaillierte Implementierung, Klassenstrukturen und API-Schnittstellen der Forstwirtschaft (Baumerei).
tags: [module, forestry, sawmill, carpentry, forrest, interfaces]
---
# Modul: Forestry (Baumerei)

Das `forestry`-Modul automatisiert den Holzfäller-Bereich: Baumfällung, Wiederaufforstung, Bewässerung, Produktionsstätten (Sägewerk & Schreinerei), Kundenbedienung (Farmis) und Forstwirts-Quests.

## 1. Klassen- & Dateiübersicht

### `Forestry.js` (Modul-Koordinator)
- **Klasse:** `Forestry`
- **Exportiert:** `async function handleForestry()`
- **Konfiguration:** `new Conf({configName: 'Forestry'})`
- **Ablauf:**
  - Ruft `RestApi.apiCall('forestry', {action: 'initforestry'})` auf.
  - Aktualisiert Baumerei-Lager via `Stock.update(body)`.
  - Lädt dynamisch konfigurierte Submodule (`modules`) via `Modules.loadModules(modules)`.
  - Übergibt `body` und eine zentrale `OrderManager`-Instanz an jedes Submodul.

### `Forrest.js` (Waldfläche)
- **Klassen:** `Tree`, `Forrest`
- **Exportiert:** `async function handleForrest(body, orderManager)`
- **Attribute:** 25 Baum-Positionen (`trees[0..24]`).
- **Ablauf `loop()`:**
  1. `update(body)`: Mappt Baumdaten auf `Tree`-Objekte (`position`, `productid`, `remain`, `waterremain`).
  2. `cut(body)`: Sucht Bäume mit `remain <= 0`. Fällt alle reifen Bäume via `cropall`.
  3. `plant(body)`: Findet Baumart, von deren Holzart am wenigsten im Lager liegt (`Stock.fromStock(2)`). Pflanzt über `autoplant`.
  4. `water(body)`: Gießt Bäume mit `waterremain <= 0` via `water`.

### `Factory.js` (Basisklasse Produktionsstätten)
- **Klasse:** `Factory`
- **Attribute:** `buildingId`, `stockId`, `slots` (Slot 1 & 2).
- **Methoden:**
  - `update(body)`: Parst Slots aus `body.datablock[2][this.buildingId].slots`.
  - `async harvest(body)`: Erntet fertige Slots (`slot.remain < 0`) via `cropproduction`.
  - `async startProduction(slotId, pid)`: Startet Produktion via `startproduction`.
  - `produceOrders(orders, body)`: Startet Aufträge aus der Order-Queue in freien Slots, sofern Rohstoffe verfügbar sind.
  - `orderRequirements(orders, orderManager)`: Leitet fehlende Vorprodukte an vorgelagerte Produktionsstufen im `OrderManager` weiter.

### `Sawmill.js` (Sägewerk)
- **Klasse:** `Sawmill extends Factory`
- **Exportiert:** `async function handleSawmill(body, orderManager)`
- **Konfiguration:** `buildingId = 1`, `stockId = 3`.

### `Carpentry.js` (Schreinerei)
- **Klasse:** `Carpentry extends Factory`
- **Exportiert:** `async function handleCarpentry(body, orderManager)`
- **Konfiguration:** `buildingId = 2`, `stockId = 4`.

### `OrderManager.js`
- **Klassen:** `Order`, `OrderManager`
- **Zweck:** Verwaltung von Produktionsaufträgen zwischen Farmis, Sägewerk und Schreinerei.
- **Methoden:**
  - `addOrder({pid, amount}, before)`: Auftrag einsortieren.
  - `getOrders(stockId)`: Aufträge gefiltert nach Lagerkategorie (z.B. Schnittholz vs. Schreinerware) abrufen.

### `Stock.js` (Baumerei-Lager)
- **Klasse:** `Stock` (Forstwirtschaft-spezifisch!)
- **Methoden:**
  - `async update(body)`: Parst Produktkatalog aus `body.datablock[4]` und Bestände aus `body.updateblock.forestry_stock`.
  - `fromStock(stockId)`: Filtert Produkte nach Lagerkategorie (`1` = Setzlinge, `2` = Stämme, `3` = Bretter, `4` = Möbel).

### `Farmis.js` & `Quests.js`
- Bedient Kunden, die am Waldrand stehen, und erfüllt Forstwirts-Quests, sobald die benötigten Hölzer vorrätig sind.

---

## 2. Externe Spielserver-Schnittstellen (MFF AJAX)

Alle Anfragen gehen an den Service `forestry.php`:

| Action | Parameter | Zweck |
| :--- | :--- | :--- |
| `initforestry` | Keine | Initialisiert den Forstwirtschaftsbereich |
| `cropall` | Keine | Fällt alle erntereifen Bäume auf einmal |
| `autoplant` | `productid` | Bepflanzt alle freien Baumplätze mit Setzling `productid` |
| `water` | Keine | Bewässert alle Bäume |
| `cropproduction` | `position`, `slot` | Erntet fertige Ware aus Gebäude (`position=1` Sägewerk, `2` Schreinerei) |
| `startproduction` | `position`, `slot`, `productid` | Startet Produktion in Slot |
| `farmis_sendorder` | `farmi_id` | Liefert Ware an Farmi-Kunden aus |

---

## 3. Python-Redesign Empfehlungen

- **Einheitliche Hierarchie:** In Python eine generische `ProductionFacility`-Klasse in `app.core.production` definieren, von der `Sawmill`, `Carpentry`, `SushiBar` etc. erben.
- **Dependency Inversion:** Der `OrderManager` sollte ein eventbasierter Scheduler sein (z.B. Python `asyncio.PriorityQueue`).
- **Typisierte Enums:**
  ```python
  from enum import IntEnum

  class ForestryStockCategory(IntEnum):
      SEEDLING = 1
      TRUNK = 2
      LUMBER = 3
      FURNITURE = 4
  ```
