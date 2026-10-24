---
title: Modul Handwerkerdorf & Steinbruch (Crafting Village & Quarry)
author: System
date: 2026-10-23
type: module
description: Spezifikation und Dokumentation des Handwerkerdorfs (Steinbruch/Mine, Arbeiterverwaltung, Ausrüstung, Steinmetz und Quests).
tags: [crafting-village, mine, handwerkerdorf, quarry, automation, modules]
---

# Modul Handwerkerdorf & Steinbruch (Crafting Village & Quarry)

Das Handwerkerdorf ist ein Wirtschafts- und Verarbeitungsbereich in MyFreeFarm. Die erste erschlossene Betriebsstätte ist der **Steinbruch (Mine)** zum Abbau von Natursteinen (z. B. Sandstein und Granit). Zukünftige Ausbaustufen umfassen den **Steinmetz** zur Veredelung sowie eine eigene **Dorf-Questreihe**.

---

## 1. Spielmechanik & Komponenten

### Steinbruch (Mine)
- **Abbauslots:** Der Steinbruch verfügt über mehrere Produktionsslots (Slot 1 für Sandstein, Slot 2 für Granit, weitere Slots mit Spielgeld oder Coins freischaltbar).
- **Materialien:**
  - **Sandstein:** Dauer 18.000s (5 Std.), Basis-Ertrag 5 Einheiten, Energieverbrauch 10 Punkte, benötigt 1 Arbeiter und 2 Ausrüstungsgegenstände.
  - **Granit:** Dauer 21.600s (6 Std.), Basis-Ertrag 5 Einheiten, Energieverbrauch 15 Punkte, benötigt 1 Arbeiter und 2 Ausrüstungsgegenstände.
- **Arbeiter (Workers):**
  - Arbeiter besitzen einen Namen, eine Energiestufe (0–100) sowie Erfahrungsstufen pro Material (z. B. Stufe 3 Sandstein mit Ertrags- oder Zeitboni).
  - Vor jedem Abbau muss die Energie ausreichend sein (Auffüllen über landwirtschaftliche Produkte).
- **Ausrüstung & Haltbarkeit (Items):**
  - Werkzeuge (Spitzhacken, Sicherheitsschuhe, Helme etc.) haben eine begrenzte Haltbarkeit (Standard 20 Einsätze).
  - Im Einsatz befindliche Ausrüstung ist an den Produktionsslot gebunden (`stock: 0`), freie Ausrüstung liegt im Inventar (`stock: 1`).
  - Abgenutzte Werkzeuge müssen im Steinbruch-Shop nachgekauft werden.

---

## 2. Externe Spielserver-Schnittstelle

Alle Aufrufe erfolgen an das zentrale Skript `ajax/farm.php`:

### Initialisierung & Status
- **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&mode=mine_init`
- **Response (`datablock`):**
  - `data.level`: Aktuelle Ausbaustufe des Steinbruchs.
  - `data.stock`: Dorf-Lagerbestand für gewonnene Steine (`{"sandstone": 5, "granite": 0}`).
  - `data.workers`: Arbeiter-Objekte mit Status, Energie, Erfahrung und aktueller `productionid`.
  - `data.items`: Werkzeug-Inventar mit Zustand (`durability`) und Verfügbarkeit (`stock`).
  - `data.productions`: Laufende Abbauslots mit verbleibender Restzeit (`remain`), Slot-ID und zugewiesenen Ressourcen.
  - `data.validenergyproducts`: Mapping von Farm-Produkt-IDs auf den Energiegewinn beim Füttern.
  - `config.material`: Material-Konfigurationen (Dauer, Yield, Energiekosten).
  - `config.items`: Shopkatalog für Werkzeugpreise und Boni.

### Ernte / Fertigstellung (`mine_finish_production`)
- **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&slot={slot}&position={pos}&mode=mine_finish_production`
- **Response (`datablock.reward`):**
  - `materials`: Gewonnene Erntemenge (z. B. `{"sandstone": "7"}`).
  - `xp`: Erfahrungsgewinn für zugewiesene Arbeiter (z. B. `{"16": {"sandstone": 13}}`).
  - `money`: Erhaltene kT (z. B. 180 kT).
  - `points`: Erhaltene Farmpunkte (z. B. 221 Punkte).

### Abbau starten (`mine_start_production`)
- **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&setup={json_setup}&mode=mine_start_production`
- **Setup-Payload (JSON-kodiert):**
  ```json
  {
    "slot": 1,
    "position": 1,
    "name": "sandstone",
    "workers": { "1": 16 },
    "items": { "1": 1321, "2": 1322 }
  }
  ```
- **Response:** Liefert im `updateblock.mine` sofort den aktualisierten Status des Steinbruchs.

### Arbeiter-Energie auffüllen (`mine_worker_refill`)
- **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&id={worker_id}&pid={product_id}&amount={amount}&mode=mine_worker_refill`
- **Parameter:**
  - `id`: Arbeiter-ID (z. B. `"16"`).
  - `pid`: ID des verwendeten Feldprodukts (z. B. `1` für Karotte).
  - `amount`: Stückzahl (z. B. `55` Karotten für 55 Energiepunkte).
- **Response:**
  - `datablock`: `1`
  - `updateblock.mine`: Enthält den aktualisierten Arbeiter mit neuer Energie (z. B. `100`).
  - `updateblock.stock`: Enthält das Farm-Lager nach Abzug der verfütterten Einheiten.

### Werkzeuge im Shop kaufen (`mine_buy_shop_item`)
- **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&name={item_name}&amount={amount}&mode=mine_buy_shop_item`
- **Parameter:**
  - `name`: Name des Werkzeugs (z. B. `shoes`, `pickaxe`, `helmet`).
  - `amount`: Anzahl der zu kaufenden Einheiten.
- **Coin-Schutz & Strategie:**
  - Es werden ausschließlich Werkzeuge gekauft, die mit kT (Spielgeld) bezahlt werden (`coins == 0`).
  - Unter den kT-Werkzeugen wird dynamisch dasjenige mit dem höchsten Erfahrungsgewinn (`xp_bonus = 0.8` für Stahlkappenschuhe `shoes`) gewählt.
  - Das Werkzeug wird automatisch nachgekauft, sobald im Rucksack weniger als 2 Einheiten mit Haltbarkeit verfügbar sind.
- **Response:**
  - `datablock`: `1`
  - `updateblock.mine`: Enthält das neue Werkzeug mit voller Haltbarkeit (`durability: 20`, `stock: 1`).
  - `updateblock.menue.bar`: Aktualisierter Kontostand in kT.

---

## 3. Automationsstrategien (`CraftingVillageService.serve`)

1. **Auto-Ernte (`auto_harvest`):**
   - Prüft in jedem Zyklus alle Slots in `mine.productions`.
   - Sobald `remain <= 0`, wird `mine_finish_production` aufgerufen und die Belohnung protokolliert.

2. **Gesteins-Auswahl (`least_stock` Balancing):**
   - Vergleicht den aktuellen Dorflager-Bestand: `sandstone_stock` vs. `granite_stock`.
   - Baut bevorzugt das Gestein mit dem geringeren Bestand ab, um ein ausgewogenes Lager für spätere Verarbeitungsstufen (Steinmetz) sicherzustellen.

3. **Optimierte Werkzeugauswahl (Höchste Erfahrungsstufe):**
   - Filtert freie Werkzeuge im Rucksack (`stock > 0`, `durability > 0`).
   - Sortiert nach dem XP-Bonus (`bonus.xp`) absteigend (z. B. Funkgerät 1.6 > Schuhe 0.8 > Spitzhacke 0.4).
   - Wählt die besten 2 verfügbaren Werkzeuge für den Abbau aus.

4. **Arbeiter-Zuweisung:**
   - Wählt untätige Arbeiter mit der höchsten Fertigkeitsstufe für das Zielmaterial und prüft, ob die Energie für den Vorgang ausreicht.

---

## 4. Pydantic v2 Datenarchitektur (`app.modules.crafting_village.models`)

- `WorkerSkill`: Punkte, Stufe und Prozentsatz pro Gesteinsart.
- `Worker`: ID, Name, Energie (`energy_percent`), Status (`is_working`), Fähigkeiten.
- `MineItem`: ID, Typ, Haltbarkeit, Status (`is_available`, `is_in_use`).
- `MineProductionSlot`: Slot-Nummer, Gestein, Ertrag, Dauer, Restzeit (`remain`, `is_ready`).
- `MineSnapshot`: Vollständiger Runtime-Zustand des Steinbruchs.
- `CraftingVillageSnapshot`: Übergreifender Zustand inkl. Steinmetz- und Quest-Platzhalter.
- `CraftingVillageSummary`: Kompakte Metriken für API und UI-Dashboard.

---

## 5. API Endpunkte (`/api/v1/crafting`)

| Methode | Pfad | Beschreibung |
| :--- | :--- | :--- |
| `GET` | `/api/v1/crafting` | Liefert Konfiguration, Zusammenfassung und aktuellen Snapshot. |
| `GET` | `/api/v1/crafting/settings` | Ruft die aktuellen Automationseinstellungen ab. |
| `PUT` | `/api/v1/crafting/settings` | Aktualisiert Automationseinstellungen (Auto-Ernte, Material etc.). |

---

## 6. UI-Dashboard-Integration

Im Web-Dashboard (`app/static/index.html`):
- Neuer Navigations-Tab **„Handwerkerdorf“** (`#tab-crafting`).
- Live-Metrik-Karten für Steinbruch-Level, Arbeiter-Status, aktive Abbaue und Dorf-Lagerbestand.
- Dynamische Slot-Anzeige mit visuellem Fortschrittsbalken und Restzeit-Countdown.
- Arbeiter-Übersicht mit interaktivem Energiebalken und Fähigkeitsleveln.
- Werkzeug-Inventarliste mit Farbcodierung für die Rest-Haltbarkeit.
- Strategie-Auswahl: *Abwechselnd (geringster Lagerbestand)*, *Nur Sandstein*, *Nur Granit*.
