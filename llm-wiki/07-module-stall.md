---
title: Modul Stall (Marktbude/Obststand) - Source-Code & Schnittstellen
author: System
date: 2026-09-11
type: module
description: Detaillierte Dokumentation des Obststandes/Marktstandes (Stall.js), Slot-Befüllung und Belohnungen.
tags: [module, stall, fruit, marketstand, interfaces]
---
# Modul: Stall (Obststand / Marktbude)

*Hinweis zur Namensgebung:* Im Code bezeichnet `stall/Stall.js` nicht den Viehstall (welcher in `farm/Shed.js` liegt), sondern den Marktstand/Obststand auf der Karte (`map.stall`).

## 1. Klassen- & Dateiübersicht

### `Stall.js`
- **Klasse:** `Stall`
- **Exportiert:** `async function handleStall()`
- **Methoden:**
  - `async loop()`: Ruft `update()` auf und iteriert über alle Marktstände via `handleStall(stallData[stallId])`.
  - `async update(body)`:
    - API-Call: `RestApi.apiCall('farm', {mode: 'stall_init'})`.
    - Synchronisiert `Stock.update(body)`.
    - Berechnet für jeden Stand die Kapazität (`fillsum = config.level[stallId][level].fillsum`).
  - `async handleStall(stall)`:
    1. **Leeren schwacher Slots:** Entfernt Früchte aus Slots, deren Füllstand unter 50% gefallen ist (`amount < fillsum * 0.5`) via `mode: 'stall_clear_slot'`.
    2. **Warenauswahl:** Sucht aus `Stock.products` Produkte der Kategorie `"ex"` (Exotische Früchte), deren Bestand größer als `stall.fillsum` ist, aufsteigend nach Menge sortiert.
    3. **Befüllen:** Befüllt freie Slots mit maximaler Menge via `mode: 'stall_fill_slot'`.
    4. **Belohnung abholen:** Falls `stall.reward` aktiv ist, wird die Belohnung abgeholt via `mode: 'stall_get_reward'`.

---

## 2. Externe Spielserver-Schnittstellen (MFF AJAX)

| Service | Mode | Parameter | Zweck |
| :--- | :--- | :--- | :--- |
| `farm` | `stall_init` | Keine | Obststand-Übersicht und Füllstände laden |
| `farm` | `stall_clear_slot` | `position, slot` | Slot räumen |
| `farm` | `stall_fill_slot` | `position, slot, pid, amount` | Früchte in Stand-Slot einfüllen |
| `farm` | `stall_get_reward` | `position` | Belohnung / Taler des Standes einsammeln |

---

## 3. Python-Implementierung (`app/modules/stall/`)

Das Obststand-Modul wurde vollständig in modernem Python implementiert und im WorkerScheduler integriert.

### 3.1 Klassen- & Modulaufbau
- **Modelle ([`models.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/stall/models.py)):**
  - `StallSlot`: Repräsentiert einen Slot (`slot_id`, `pid`, `product_name`, `amount`, `time`, `is_empty`).
  - `MarketStall`: Repräsentiert eine Marktbude (`position`, `level`, `fillsum`, `reward_ready`, `points`, `farmi_count`, `slots`).
  - `StallSnapshot` & `StallSummary`: Runtime-Zustand und DTOs für Dashboard und REST-API.
- **Service ([`service.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/stall/service.py)):**
  - `FruitStallService`:
    - `init_remote(stock_service)`: Lädt `stall_init`, ermittelt dynamisch `fillsum` für das jeweilige Standlevel und baut den Snapshot auf.
    - `collect_rewards()`: Holt für alle Stände mit `reward_ready == True` die Belohnungen via `mode=stall_get_reward` ab.
    - `clear_depleted_slots()`: Räumt Slots mit Früchtebestand unter dem Schwellenwert (`amount < fillsum * clear_threshold_percent`, Standard 50%) via `mode=stall_clear_slot`.
    - `fill_free_slots(stock_service)`:
      - Wählt Früchte der Kategorie `"ex"` (Exoten) aus dem Lagerbestand, deren Menge abzüglich der Mindestreserve (`min_stock_reserve = 500`) mindestens `fillsum` beträgt.
      - Sortiert absteigend nach verfügbarem Lagerbestand (größte Bestände zuerst).
      - Verhindert Doppelbelegungen derselben Frucht im selben Stand.
      - Befüllt freie Slots via `mode=stall_fill_slot&position={pos}&slot={slot}&pid={pid}&amount={fillsum}`.
    - `serve(stock_service)`: Orchestriert den gesamten Wartungszyklus im Worker.
- **REST-API ([`app/api/endpoints/stall.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/api/endpoints/stall.py)):**
  - `GET /api/v1/stall`: Live-Status, Stände, Slots und Belohnungen.
  - `GET/PUT /api/v1/stall/settings`: Konfigurationsverwaltung (`enabled`, `auto_clear_depleted`, `clear_threshold_percent`, `auto_fill_slots`, `auto_collect_reward`, `min_stock_reserve`).
  - `POST /api/v1/stall/action/collect`: Manuelles Abholen der Belohnungen.
  - `POST /api/v1/stall/action/fill`: Manuelles Bestücken freier Slots.
  - `POST /api/v1/stall/action/clear`: Manuelles Räumen leerer/schwacher Slots.
- **Worker-Scheduler-Integration:**
  - Verankert als Schritt 12 im Hauptzyklus von `WorkerScheduler` (`app/worker/scheduler.py`).

