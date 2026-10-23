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

---

## 3. Pydantic v2 Datenarchitektur (`app.modules.crafting_village.models`)

- `WorkerSkill`: Punkte, Stufe und Prozentsatz pro Gesteinsart.
- `Worker`: ID, Name, Energie (`energy_percent`), Status (`is_working`), Fähigkeiten.
- `MineItem`: ID, Typ, Haltbarkeit, Status (`is_available`, `is_in_use`).
- `MineProductionSlot`: Slot-Nummer, Gestein, Ertrag, Dauer, Restzeit (`remain`, `is_ready`).
- `MineSnapshot`: Vollständiger Runtime-Zustand des Steinbruchs.
- `CraftingVillageSnapshot`: Übergreifender Zustand inkl. Steinmetz- und Quest-Platzhalter.
- `CraftingVillageSummary`: Kompakte Metriken für API und UI-Dashboard.

---

## 4. API Endpunkte (`/api/v1/crafting`)

| Methode | Pfad | Beschreibung |
| :--- | :--- | :--- |
| `GET` | `/api/v1/crafting` | Liefert Konfiguration, Zusammenfassung und aktuellen Snapshot. |
| `GET` | `/api/v1/crafting/settings` | Ruft die aktuellen Automationseinstellungen ab. |
| `PUT` | `/api/v1/crafting/settings` | Aktualisiert Automationseinstellungen (Auto-Ernte, Material etc.). |

---

## 5. UI-Dashboard-Integration

Im Web-Dashboard (`app/static/index.html`):
- Neuer Navigations-Tab **„Handwerkerdorf“** (`#tab-crafting`).
- Live-Metrik-Karten für Steinbruch-Level, Arbeiter-Status, aktive Abbaue und Dorf-Lagerbestand.
- Dynamische Slot-Anzeige mit visuellen Fortschrittsbalken und Restzeit-Countdowns.
- Arbeiter-Übersicht mit interaktivem Energiebalken und Fähigkeitsleveln.
- Werkzeug-Inventarliste mit Farbcodierung für die Rest-Haltbarkeit.
- Zukunftskarten für den Steinmetz und die Handwerker-Quests.
