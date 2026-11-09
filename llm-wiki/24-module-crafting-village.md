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

### Steinmetz (Stonemason Workshop)
- **Werkstatt & Werkbänke:** Der Steinmetz veredelt die im Steinbruch gewonnenen Natursteine zu Werksteinen, Skulpturen und Ziegeln für Kunden.
- **Slots / Werkbänke:** Werkbank-Slots 1 bis 4 (Slot 1 ab Stufe 1, Slot 2 ab Stufe 5, Slot 3 ab Stufe 10, Slot 4 ab Stufe 15 freigeschaltet).
- **Kundenaufträge (Customers):** Bis zu 4 Kunden warten mit konkreten Wünschen (z. B. Sandstein-Ziegel, Granitziegel, Ziegelmauerstein).
- **Materialbedarf:** Kundenaufträge verbrauchen Natursteine direkt aus dem Dorflager (`mine.stock`, z. B. 3x Sandstein).
- **Meißel & Kachel-Raster:** Das Werkstück besitzt ein Raster (z. B. 4x4 Kacheln). Durch Meißel-Werkzeuge werden erlaubte Kacheln (`allowed`) schrittweise abgetragen.
- **Werkzeug-Katalog:**
  - Meißel 1..4 kosten Spielgeld (kT), z. B. Meißel (1x1), Langer Meißel (2x1), Dünner Meißel (1x2), Großer Meißel (2x2).
  - Meißel 5..7 (Riesenmeißel 3x3, Kreuzmeißel, Rundmeißel) kosten Coins.
- **Belohnungen & Punkte:** Fertiggestellte Kundenaufträge liefern Farm-Punkte, Spielgeld (kT), Steinmetz-Punkte sowie Multiplikatoren (`reward_multi`).

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

### Steinmetz Initialisierung (`stonemason_init`)
- **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&mode=stonemason_init`
- **Response (`datablock`):**
  - `data.level`: Stufe der Steinmetz-Werkstatt.
  - `data.points`: Aktuelle Steinmetz-Punkte.
  - `data.level_percent`: Fortschritt zur nächsten Stufe in Prozent.
  - `data.level_rest`: Verbleibende Punkte bis zum nächsten Stufenaufstieg.
  - `data.tools`: Mapping von Werkzeug-ID auf die Stückzahl im Inventar (z. B. `{"1": 11}`).
  - `data.customers`: Wartende und zugewiesene Kunden mit Position (1..4), Produktwunsch (`pid`), Werkbank-Slot (`slot`), Belohnungsmultiplikatoren (`reward_multi`) und Kachel-Raster (`grid`).
  - `config.slots`: Slot-Freischaltlevel (Slot 1: Stufe 1, Slot 2: Stufe 5, Slot 3: Stufe 10, Slot 4: Stufe 15).
  - `config.tools`: Meißel-Katalog mit Dimensionen (x, y), Bearbeitungsdauer und Preisen.
  - `config.products`: Skulpturen-Katalog mit benötigtem Gestein (`sandstone`, `granite`), Raster-Dimensionen (x, y) und erlaubten Meißel-Kacheln (`allowed`).

### Steinmetz Aktionen (HAR-02 & HAR-03 verifiziert)
- **Kunde zuweisen (`stonemason_set_slot`):**
  - **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&customerid={id}&slot={slot_id}&mode=stonemason_set_slot`
  - **Funktion:** Weist einen wartenden Kunden einer freien Werkbank zu. Zieht die benötigten Rohsteine (`sandstone`, `granite`) sofort aus dem Dorflager (`mine.stock`) ab.
  - **Response:** `{"datablock": 1, "updateblock": {"stonemason": {...}, "mine": {...}, "stock": {...}}}`
- **Kachel bearbeiten (`stonemason_click_tile`):**
  - **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&customerid={id}&tile={tile_id}&tool={tool_id}&mode=stonemason_click_tile`
  - **Funktion:** Startet das Behauen einer erlaubten Kachel (`allowed`) im Kachel-Raster mit dem angegebenen Meißel-Werkzeug (Standard Werkzeug 1). Verbraucht 1 Meißel und setzt die Kachel auf `remain = duration` (z. B. 1800s).
  - **Response:** `{"datablock": 1, "updateblock": {"stonemason": {...}}}`
- **Auftrag abschließen (`stonemason_finish_grid`):**
  - **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&slot={slot_id}&mode=stonemason_finish_grid`
  - **Funktion:** Schließt eine zu 100% gemeißelte Skulptur (alle Kacheln in `allowed` fertiggestellt) ab. Schüttet Farmpunkte, Spielgeld (kT), Steinmetz-Punkte und ggf. Sterne/Errungenschaften aus.
- **Meißel nachkaufen (`stonemason_buy_tool`):**
  - **Endpunkt:** `GET https://s{server}.myfreefarm.de/ajax/farm.php?rid={rid}&tool={tool_id}&currency={amount}&mode=stonemason_buy_tool`
  - **Funktion:** Kauft Meißelpakete (z. B. 10 Meißel für 500 kT mit `tool=1&currency=500`). Werkzeuge 1..4 kosten ausschließlich kT (strikter Coin-Schutz).

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

5. **Steinmetz-Automation (`serve_stonemason`):**
   - **Fertige Skulpturen abholen:** Prüft belegte Werkbänke. Wenn alle Kacheln gemeißelt und deren Dauer abgelaufen ist (`remain <= 0`), wird `stonemason_finish_grid` ausgeführt.
   - **Kacheln schlagen:** Wenn aktuell kein Meißelvorgang aktiv ist (`is_working == False`), wird die nächste freie Kachel aus `allowed` gewählt und mit Meißel (Tool 1) gestartet.
   - **Meißel-Nachkauf:** Wenn der Meißelbestand 0 erreicht und `auto_buy_stonemason_tools` aktiv ist, wird automatisch das 10er-Paket für 500 kT nachgekauft.
   - **Kunden-Zuweisung (Beste Belohnung):** Freie, freigeschaltete Werkbänke werden mit wartenden Kunden belegt, sofern ausreichend Rohmaterial im Dorflager vorhanden ist. Das System berechnet die effektive Belohnung jedes Kunden (calculate_rewards: Spielgeld kT, Farm-Punkte und Steinmetz-Punkte inklusive Multiplikatoren) und bewertet sie anhand des gewählten Fokus (stonemason_reward_priority):
     - overall (Standard / Beste Gesamtbelohnung): Gewichteter Gesamtscore (sm_points * 100 + money + points * 0.1).
     - stonemason_points: Priorisiert maximalen Steinmetz-Punktegewinn für schnellen Werkstatt-Levelaufstieg.
     - money: Priorisiert maximalen kT-Ertrag.
     - points: Priorisiert maximale Farm-Erfahrungspunkte.

---

## 4. Pydantic v2 Datenarchitektur (`app.modules.crafting_village.models`)

- `WorkerSkill`: Punkte, Stufe und Prozentsatz pro Gesteinsart.
- `Worker`: ID, Name, Energie (`energy_percent`), Status (`is_working`), Fähigkeiten.
- `MineItem`: ID, Typ, Haltbarkeit, Status (`is_available`, `is_in_use`).
- `MineProductionSlot`: Slot-Nummer, Gestein, Ertrag, Dauer, Restzeit (`remain`, `is_ready`).
- `MineSnapshot`: Vollständiger Runtime-Zustand des Steinbruchs.
- `StonemasonToolConfig`: Meißel-Typ, Dimensionen (x, y), Bearbeitungsdauer, Kosten.
- `StonemasonProductConfig`: Skulpturen-Katalog, Materialbedarf, Raster, erlaubte Kacheln.
- `StonemasonCustomer`: Kunde in Warteschlange oder auf Werkbank mit Produktwunsch, Multiplikatoren und Kachelfortschritt.
- `StonemasonSlotConfig`: Werkbank-Konfiguration mit Freischaltstufe.
- `StonemasonSnapshot`: Vollständiger Runtime-Zustand des Steinmetzes (Stufe, Punkte, Werkzeuge, Kunden, Slots).
- `CraftingVillageSnapshot`: Übergreifender Zustand inkl. Steinbruch, Steinmetz und Dorf-Quests.
- `CraftingVillageSummary`: Kompakte Metriken für API und UI-Dashboard.

---

## 5. API Endpunkte (`/api/v1/crafting`)

| Methode | Pfad | Beschreibung |
| :--- | :--- | :--- |
| `GET` | `/api/v1/crafting` | Liefert Konfiguration, Zusammenfassung und aktuellen Snapshot. |
| `GET` | `/api/v1/crafting/settings` | Ruft die aktuellen Automationseinstellungen ab. |
| `PUT` | `/api/v1/crafting/settings` | Aktualisiert Automationseinstellungen (Auto-Ernte, Steinmetz etc.). |
| `POST` | `/api/v1/crafting/action/stonemason_set_slot` | Weist einen Kunden einer Werkbank zu. |
| `POST` | `/api/v1/crafting/action/stonemason_click_tile` | Startet das Behauen einer Kachel. |
| `POST` | `/api/v1/crafting/action/stonemason_finish_grid` | Schließt eine fertige Skulptur ab. |
| `POST` | `/api/v1/crafting/action/stonemason_buy_tool` | Kauft Meißelpakete für kT nach. |

---

## 6. UI-Dashboard-Integration

Im Web-Dashboard (`app/static/index.html`):
- Neuer Navigations-Tab **„Handwerkerdorf“** (`#tab-crafting`).
- Live-Metrik-Karten für Steinbruch-Level, Arbeiter-Status, aktive Abbaue und Dorf-Lagerbestand.
- Dynamische Slot-Anzeige mit visuellem Fortschrittsbalken und Restzeit-Countdown.
- Arbeiter-Übersicht mit interaktivem Energiebalken und Fähigkeitsleveln.
- Werkzeug-Inventarliste mit Farbcodierung für die Rest-Haltbarkeit.
- **Steinmetz-Werkstatt-Bereich:**
  - Statuskarte mit Werkstatt-Stufe, Steinmetz-Punkten und Meißel-Bestand.
  - Interaktiver Stufen-Fortschrittsbalken mit Anzeige der benötigten Punkte bis zum nächsten Aufstieg.
  - Werkbänke (Slots 1..4): Anzeige von Belegung, geschnitztem Produkt, Kachelfortschritt und Status.
  - Wartende Kunden: Kundenliste mit gewünschtem Produkt, Material-Verfügbarkeitsprüfung (grün/rot) und Multiplikatoren.
- Strategie-Auswahl: *Abwechselnd (geringster Lagerbestand)*, *Nur Sandstein*, *Nur Granit*.
