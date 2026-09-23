---
title: Modul FarmersMarket - Source-Code & Schnittstellen
author: System
date: 2026-09-23
type: module
description: Detaillierte Dokumentation von Marktplatz, Blumengestecken (Nursery), 36 Blumenbeeten (FlowerArea), Schauslots (FlowerSlots), Marktkunden (Farmis) und Tierzucht (PetBreed).
tags: [module, farmersmarket, nursery, flowerarea, flowerslots, farmis, petbreed, interfaces]
---
# Modul: FarmersMarket (Marktplatz, Blumen & Tierzucht)

Das `farmersmarket`-Modul umfasst die Automatisierung des Marktbereichs in Teichlingen (Dorf 2): Die Blumengesteck-Werkstatt (`Nursery`), die 36 Blumenbeete der Blumenwiese (`FlowerArea`), die Schau-Slots im Dorf (`FlowerSlots`), die Markt-Kunden (`Farmis`) mit Auftragsmanager (`FlowerOrderManager`) sowie die Tierzuchtstation (`PetBreed`).

---

## 1. Verifizierte Spielserver-Schnittstellen (MFF AJAX)

Alle Aktionen des Bauernmarkts laufen über den Endpunkt `farm.php` auf dem jeweiligen Spielserver (`farm=1, position=1`):

| Service | Mode | Parameter | Zweck & Verhalten |
| :--- | :--- | :--- | :--- |
| `farm` | `getfarms` | `farm=1, position=0` | Liefert den kompletten `updateblock.farmersmarket` Zustand (kein separates Init nötig) |
| `farm` | `nursery_harvest` | `farm=1, position=1, id={slot}, slot={slot}` | Fertiges Gesteck aus Slot abholen |
| `farm` | `nursery_startproduction` | `farm=1, position=1, id={pid}, pid={pid}, slot={slot}` | Produktion eines Gestecks starten |
| `farm` | `flowerarea_harvest_all` | `farm=1, position=1` | Gesamte Blumenwiese auf einmal abernten |
| `farm` | `flowerarea_autoplant` | `farm=1, position=1, set=0, pid={pid}` | Gesamte Blumenwiese komplett mit Blumensorte `{pid}` bepflanzen |
| `farm` | `flowerarea_water_all` | `farm=1, position=1` | Alle bepflanzten Beete der Blumenwiese auf einmal gießen |
| `farm` | `flowerslot_remove` | `farm=1, position=1, set={slot}:1` | Verwelktes Gesteck aus Schau-Slot entfernen |
| `farm` | `flowerslot_water` | `farm=1, position=1, set={slot}:1` | Gesteck im Schau-Slot gießen |
| `farm` | `flowerslot_plant` | `farm=1, position=1, set=1:{pid}` | Neues Gesteck im Schau-Slot ausstellen |
| `farm` | `handleflowerfarmi` | `farm=1, position=1, id={id}, farmi={id}, status=1` | Farmi am Marktstand bedienen |
| `farm` | `petbreed_init` | Keine | Tierzuchtstation öffnen *(nur bei aktiver Tierzucht)* |
| `farm` | `petbreed_harvest` | `slot={slot}` | Zuchtergebnis einsammeln *(nur bei aktiver Tierzucht)* |
| `farm` | `petbreed_start` | `slot={slot}, pet={pet}, tool={tool}` | Neue Zucht starten *(nur bei aktiver Tierzucht)* |

---

## 2. Architektur & Python-Implementierung (`app/modules/farmersmarket/`) ✅

Das Modul ist vollständig in modernem, typisiertem Python (Pydantic v2, Asyncio, HTTPX) implementiert und nahtlos in den Worker-Scheduler integriert:

```
app/modules/farmersmarket/
├── __init__.py           # Zentrale Modulexporte
├── models.py             # Pydantic v2 DTOs (NurserySlot, FlowerField, MarketFarmi, FarmersMarketSummary, etc.)
├── order_manager.py      # FlowerOrderManager (Bedarfsermittlung aus Kundenwünschen)
├── nursery.py            # NurseryService (Gärtnerei & Gesteck-Binden)
├── flower_area.py        # FlowerAreaService (36 Blumenbeete: Ernten, Pflanzen, Wässern)
├── flower_slots.py       # FlowerSlotsService (Schau-Slots in Dorf 2)
├── farmis.py             # MarketFarmisService (Marktkunden bedienen & Rohstoff-Grasping)
├── petbreed.py           # PetBreedService (Tierzucht - standardmäßig inaktiv)
└── service.py            # FarmersMarketService (Haupt-Orchestrator)
```

---

## 3. Modul-Details & Domänenlogik

### 3.1 Gärtnerei (`NurseryService`)
- **Server-Verhalten bei leerem Slot 1:**
  Wenn Slot 1 leer/fertig geerntet ist, sendet der MFF-Server den Slot 1 **nicht** im Dictionary `nursery.slots` mit. Der Service erkennt dies und initialisiert Slot 1 automatisch als freien Produktionsplatz (`slot_id=1, remain=0, block=0, coins=0`).
- **Coins-Schutz:**
  Slots mit `block == 1` oder `coins > 0` (z. B. Slot 2 für 500k kT oder Slot 3 für 5 Coins) werden strikt ignoriert.
- **Katalog-Namen:**
  Rezeptnamen werden dynamisch aus dem zentralen `StockService`-Katalog aufgelöst, da Upstream-Daten keine Klartextnamen enthalten.
- **Vermeidung von Überproduktion:**
  Bereits in aktiven Slots laufende Produktionen werden berücksichtigt, sodass keine doppelten Gestecke parallel gestartet werden, solange der Bedarf gedeckt ist.

### 3.2 Blumenwiese (`FlowerAreaService`)

- **36 Beete im 6x6-Raster:**
  Die Blumenwiese in Teichlingen (Dorf 2) umfasst 36 Beete (`FlowerField`, Position 1 bis 36).
- **Zustandsmodell & Server-Parsing (`updateblock.farmersmarket.flower_area`):**
  - **Belegte Beete:** Der Server liefert ein Dictionary der Positionen `"1"` bis `"36"` mit Feldeigenschaften (`pid`, `remain`, `water_remain`, `duration`, `createdate`).
  - **Vollständig abgeräumte Wiese:** Nach einer Gesamternte (`flowerarea_harvest_all`) liefert der Server ein leeres Array (`"flower_area": []`). Der Service fängt diesen Fall robust ab und initialisiert 36 leere Beete (`pid=None, remain=0, water_remain=0`).
  - **Status-Kriterien:**
    - `remain < 0`: Pflanze ist ausgewachsen und erntereif (`field.is_ready`).
    - `water_remain < 0`: Beet ist trocken und muss bewässert werden (`field.needs_water`).
    - `pid is None`: Beet ist unbepflanzt (`field.is_empty`).
- **Batch-Automatisierungslogik (Server-optimiert via `farm=1, position=1`):**
  - **Gesamternte (`harvest` via `mode=flowerarea_harvest_all`):**
    - Erntet alle bepflanzten Beete der Wiese mit einer einzigen AJAX-Transaktion ab.
    - **Synchronitäts-Schutz:** Die Ernte wird nur ausgeführt, wenn **ausnahmslos alle** bepflanzten Beete erntereif sind (`all(f.is_ready for f in planted)`). Dies verhindert das vorzeitige Abräumen unfertiger Pflanzen und hält die Wachstumszyklen aller 36 Beete synchron.
  - **Vollflächiges Anpflanzen (`plant` via `mode=flowerarea_autoplant&set=0&pid={pid}`):**
    - Sind Beete frei (`is_empty`), bepflanzt der Autoplant-Befehl alle freien Beete auf einen Schlag mit derselben Blumensorte.
    - **Lager-Nivellierung (`_get_best_flower_seed`):** Wählt die Blumensorte (Kategorie `'fl'`), von der aktuell der **geringste positive Lagerbestand** (`amount > 0`) vorhanden ist. Dadurch werden Blumen gleichmäßig nachproduziert und Bestände ausgeglichen.
    - **Coin- & Sonderitem-Filter (`price > 0`):** Spezial-Items oder nicht regulär pflanzbare Blumen (z. B. *Tigerlilie* PID 189 mit Preis `0.0`) werden ausgeschlossen, um Serverfehler (`datablock: 0`) zu verhindern.
  - **Gesamtbewässerung (`water` via `mode=flowerarea_water_all`):**
    - Bewässert alle bepflanzten Beete der Blumenwiese auf einmal (setzt `water_remain` auf 86.400 s).
    - Wird ausgeführt, sobald mindestens ein bepflanztes Beet Wasser benötigt (`any(f.needs_water for f in fields)`).
- **Optimale Zyklus-Reihenfolge:**
  1. `harvest()`: Reife Blumen vollständig abernten.
  2. `plant()`: Leere Beete sofort vollflächig neu bepflanzen.
  3. `water()`: **Im Anschluss** alle Beete gießen, damit frische Saat unmittelbar im selben Worker-Durchlauf bewässert wird.
- **Kompakte Schnittstellen-Übersicht:**

  | Aktion | Parameter | Server-Verhalten & Rückgabe |
  | :--- | :--- | :--- |
  | **Status** | `mode=getfarms&farm=1&position=0` | Liefert `updateblock.farmersmarket.flower_area` als Positions-Map (`"1"`..`"36"`) |
  | **Ernten** | `mode=flowerarea_harvest_all&farm=1&position=1` | Erntet alle Beete; Server antwortet mit leerem Array `flower_area: []` |
  | **Pflanzen** | `mode=flowerarea_autoplant&farm=1&position=1&set=0&pid={pid}` | Bepflanzt alle freien Beete mit Blumensorte `{pid}` |
  | **Gießen** | `mode=flowerarea_water_all&farm=1&position=1` | Setzt `water_remain` aller bepflanzten Beete auf 86.400 s |

### 3.3 Marktkunden & Auftragsmanager (`MarketFarmisService`, `FlowerOrderManager`)
- **Zyklus-Isolation:**
  `FlowerOrderManager.clear()` wird zu Beginn jedes Worker-Zyklus aufgerufen, damit Bedarfe sich nicht über mehrere Zyklen hinweg kaskadierend aufsummieren.
- **Gemüse- & Hofprodukt-Grasping (`category == 'v'`):**
  Fordert ein Farmi normale Ackerfrüchte (z. B. Heidelbeeren, Oliven) und der Lagerbestand reicht knapp nicht aus, wird ein sicherer Zukauf via `StockService.grasp_products(...)` unter Wahrung des Mindestguthabens (`min_credit_kt`) ausgelöst.
- **Bedarfsweiterleitung:**
  Fehlende Blumengestecke (`fla`) werden beim `FlowerOrderManager` registriert und lösen im selben Zyklus die Produktion in der Gärtnerei aus.

### 3.4 Schau-Slots (`FlowerSlotsService`)
- Räumt verwelkte Gestecke ab (`flowerslot_remove`).
- Gießt aktive Ausstellungsstücke (`flowerslot_water`).
- Bestückt leere Schau-Slots automatisch mit vorrätigen Gestecken aus dem Lager (`flowerslot_plant`).

### 3.5 Tierzucht (`PetBreedService`) – Konfigurationsgemäß inaktiv
- Vollständig typisiertes Modul für Zuchtslots, Tierfütterung und Quests.
- **Standardmäßig deaktiviert:** Über `FarmersMarketConfig.pet_breed_enabled = False` wird die Zucht komplett übersprungen. Es werden **null Netzwerk-Requests** abgesetzt.

---

## 4. REST-API & Konfiguration

### Endpunkte
- `GET /api/v1/farmersmarket`: Liefert Live-Status aller Subdomänen (`FarmersMarketSummary`).
- `GET /api/v1/farmersmarket/settings`: Aktuelle Einstellungen abfragen.
- `PUT /api/v1/farmersmarket/settings`: Einstellungen zur Laufzeit anpassen.

### Konfigurationsmodell (`FarmersMarketConfig`)
```python
class FarmersMarketConfig(BaseModel):
    enabled: bool = True
    nursery_enabled: bool = True
    flower_area_enabled: bool = True
    flower_slots_enabled: bool = True
    farmis_enabled: bool = True
    pet_breed_enabled: bool = False  # Vorgegeben: Inaktiv
    pet_daily_parts: bool = True
```

---

## 5. Dashboard-Integration

Im Web-Dashboard (`app/static/index.html`) steht ein eigener Tab **"Bauernmarkt"** zur Verfügung:
- **Statuskarten:** Gärtnerei-Status, Blumenwiese-Belegung, Schau-Slot-Besetzung, Tierzucht-Inaktivitäts-Badge.
- **36-Beete-Grid:** Interaktive Vorschau aller 36 Blumenbeete mit farblicher Unterscheidung (Bereit, Wachsend, Leer).
- **Werkstatt- & Kundenlisten:** Detaillierte Übersicht über Werkstattslots und wartende Farmis.
