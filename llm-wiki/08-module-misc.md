---
title: Spezial- & Hilfsmodule (Insecthotel, Foodworld, Events, Helpers)
author: System
date: 2026-09-13
type: module
description: Detaillierte Dokumentation von Insektenhotel, Foodworld-Gastronomie, Liefer-Events, Windmühle und Täglichen Boni.
tags: [module, insecthotel, foodworld, windmill, bonus, events, interfaces]
---
# Spezial- und Hilfsmodule

Dieses Dokument beschreibt die spezialisierten Subsysteme und täglichen Helper-Skripte des Projekts.

## 1. Modul Insektenhotel (`app/modules/insecthotel/`)

Das Insektenhotel ist ein passives Produktionsgebäude auf der Landkarte (freigeschaltet ab Level 29 für 125.000 kT), das kontinuierlich Käsetaler (kT) und Erfahrungspunkte (XP) über die Pflege von Insektenpopulationen erwirtschaftet.

### 1.1 Spielmechanik & Futter-System
- **Simulationsintervall:** Alle 4 Stunden (14.400 Sekunden) führt der Server einen Simulationsschritt durch.
- **Fütterung & Zufriedenheit:**
  - Jede der 6 Insektenarten akzeptiert genau 4 spezifische Ackerfrüchte/Gemüse/Beeren (insgesamt 12 Früchte: Radieschen 19, Erdbeeren 20, Tomaten 21, Zwiebeln 22, Spinat 23, Blumenkohl 24, Kartoffeln 26, Zucchini 31, Heidelbeeren 32, Himbeeren 33, Johannisbeeren 34, Brombeeren 35).
  - Jede Frucht liefert bei Anwesenheit im Futterlager einen festen Zufriedenheitsbonus.
  - Pro 4h-Zyklus verfällt die Zufriedenheit um `happiness_decay` (2 bis 5 Punkte).
- **Populationsdynamik:**
  - Unterhalb der Verlustgrenze (`min_happiness`, z. B. 20 bei Bienen, 50 bei Schmetterlingen) sinkt die Population um `population_loss` (-2 bis -15 Tiere/4h).
  - Im neutralen Bereich bleibt die Population unverändert ($\pm 0$).
  - Oberhalb der Wachstumsgrenze (`max_happiness`, z. B. 50 bei Bienen, 65 bei Schmetterlingen) wächst die Population um `population_gain` (+2 bis +15 Tiere/4h).
- **Kasse (Checkout):**
  - Erträge sammeln sich in der Kasse (Limits je nach Level: 3.000 kT / 60.000 Punkte bis 100.000 kT / 2.000.000 Punkte).
  - Ist die Kasse voll, verfallen weitere Erträge.

### 1.2 Upstream-Schnittstellen
| Modus / Endpunkt | Parameter | Zweck |
| :--- | :--- | :--- |
| `insecthotel_init` | `mode=insecthotel_init` | Gesamtstatus (Slots, Populationen, Futterlager, Kasse) abrufen |
| `insecthotel_set_stockslot` | `slot={id}, pid={pid}, amount={count}` | Bestimmten Futter-Lagerslot aus dem Hauptlager auffüllen |
| `insecthotel_delete_stockslot` | `slot={id}` | Futter-Lagerslot leeren und Vorrat zurück ins Hauptlager buchen |
| `insecthotel_collect_checkout` | `mode=insecthotel_collect_checkout` | Hotelkasse leeren und Erträge dem Spielerkonto gutschreiben |

### 1.3 Python-Architektur (`app/modules/insecthotel/`)
- **Modelle ([`models.py`](file:///c:/Projekte/pyFarm/app/modules/insecthotel/models.py)):**
  - `InsectNicheSlot`: Nistplatz (ID, Name, Level, Population, Zufriedenheit, Decay, Limits, Pflanzen-Map, Ertrag).
  - `InsectStockSlot`: Futter-Lagerslot (PID, Produktname, Menge, Kapazität, Füllstand).
  - `InsectCheckout`: Kasse (kT, Punkte, Grenzwerte, Füllgrad-Berechnung).
  - `InsectHotelSnapshot` & `InsectHotelSummary`: Runtime-Zustand, Ziel-PIDs, Strategie und Dashboard-DTOs.
- **Planner & Optimierung ([`planner.py`](file:///c:/Projekte/pyFarm/app/modules/insecthotel/planner.py)):**
  - Dynamischer Kombinations- und Rotationsplaner.
  - Erkennt gefährdete Bestände (`get_endangered_species`) und wechselt dynamisch zwischen:
    - **Rettungs- & Twin-Wachstumsset (`RESCUE_TWIN_GROWTH_PIDS`):** `[34, 35, 33, 26, 31, 20, 19, 22]` mit Erdbeeren (PID 20) zur Rettung von Wildbienen (+0.4) und Stabilisierung von Ohrwürmern (0.0), während Schmetterlinge (+1.25) und Marienkäfer (+0.75) wachsen.
    - **Triple-Wachstumsset (`TRIPLE_GROWTH_PIDS`):** `[34, 35, 33, 26, 31, 24, 19, 22]` mit Blumenkohl (PID 24) für gleichzeitiges Wachstum aller 3 Zielarten (Schmetterling +1.75, Marienkäfer +0.75, Schwebfliegen +0.50).
- **Service ([`service.py`](file:///c:/Projekte/pyFarm/app/modules/insecthotel/service.py)):**
  - `InsectHotelService`:
    - `init_remote(stock_service)`: Snapshot aus `insecthotel_init` aufbauen und Zielplan berechnen.
    - `rotate_slots(stock_service, force=False)`: Tauscht Futterfächer via `insecthotel_delete_stockslot` und belegt freie Fächer mit Ziel-Pflanzen.
    - `refill_stock(stock_service, force=False)`: Lagerslots auffüllen, wenn der Füllstand um >20% unter die Kapazität sinkt (oder bei `force=True`). Schützt einen Mindestpuffer von 50 Einheiten im Hauptlager (`min_stock_reserve`).
    - **Automatischer Futterzukauf (`auto_buy_feed`):** Reicht der Lagerbestand im Hauptlager nach Abzug der Reserve nicht aus, um ein Futterfach vollständig zu befüllen, beschafft der Service die Fehlmengen vollautomatisch via `stock_service.grasp_products(...)` über den Spielermarkt bzw. direkt beim NPC-Saatguthändler zum offiziellen Katalogpreis.
    - `collect_checkout(force=False)`: Kasse leeren, wenn Geld oder Punkte >= 50% des Limits erreichen (oder bei manuellem Aufruf).
    - `serve(stock_service)`: Zyklus-Abarbeitung für den Scheduler (Init $\rightarrow$ Kasse $\rightarrow$ Rotation $\rightarrow$ Auffüllen).
- **REST-API (`app/api/endpoints/insecthotel.py`):**
  - `GET /api/v1/insecthotel`: Status, Snapshot, Strategie und gefährdete Arten.
  - `GET/PUT /api/v1/insecthotel/settings`: Konfiguration (`enabled`, `strategy`, `priority_species`, `auto_rotate_slots`, `min_safety_happiness`, `auto_refill_stock`, `auto_collect_checkout`, `refill_threshold_percent`, `checkout_threshold_percent`, `min_stock_reserve`, `auto_buy_feed`).
  - `POST /api/v1/insecthotel/action/checkout?force=true`: Manuelle Kassenleerung.
  - `POST /api/v1/insecthotel/action/rotate?force=true`: Manuelle Futterplatz-Optimierung/Rotation.
  - `POST /api/v1/insecthotel/action/refill?force=true`: Manuelle Futterlager-Auffüllung.

---

## 2. Modul Foodworld / Picknick-Bereich (`foodworld.php`)

Das Foodworld-System ist der Gastronomiebereich mit 4 Küchenbuden (Getränkebude, Imbissbude, Konditorei, Eisdiele) und Restauranttischen zur Gästebewirtung.

### 2.1 Spielmechanik & Währung
- **Währung:** Restaurant-Gäste zahlen **direkt in Käsetalern (kT)**! Die Abrechnung beim Kassieren (`action: cash`) setzt sich zusammen aus:
  $$\text{Gutschrift} = \text{Basispreis (100\% NPC-Katalogwert)} + \text{Trinkgeld (Tip)} + \text{Restaurant-Bonus}$$
  Dadurch zahlen Gäste ca. **120% bis 125% des NPC-Wertes** – und das völlig **ohne die 10% Marktgebühr**!
- **Marktfähigkeit der Foodworld-Produkte:** Alle hergestellten Speisen (Kategorie `fw`, PIDs 130–169 und 450–485) liegen als reguläre Fertigwaren im Hauptlager und **können uneingeschränkt auf dem Marktplatz verkauft werden**.
- **Erfahrungspunkte & Level-Strategie:** Gäste an den Tischen geben direkt **0 Erfahrungspunkte (XP)**. Das Foodworld-Modul ist primär eine hocheffiziente **kT-Generierungsmaschine**. Die optimale Punkte-Strategie besteht darin, die Millionen an erwirtschafteten kT gezielt zum Freikauf von **Hauptquests** (welche hunderttausende bis Millionen Punkte abwerfen) und zur Finanzierung von High-XP-Pflanzen auf den Äckern einzusetzen.

### 2.2 Verifizierte Spielserver-Schnittstellen (`foodworld.php`)
| Aktion | Parameter | Zweck & Verhalten |
| :--- | :--- | :--- |
| `foodworld_init` | `id=0, table=0, chair=0` | Lädt Gesamtstatus (Küchen, Slots, Tische, Gäste, Rezepte, Quests) |
| `crop` | `id=0, table={building_id}, chair={slot_id}` | Fertig zubereitete Speise aus Küchenslot abholen |
| `production` | `id={product_id}, table={building_id}, chair={slot_id}` | Speisenzubereitung in freiem Küchenslot starten |
| `dropped` | `id={farmi_id}, table={table_id}, chair={chair_id}` | Wartenden Gast an freien Stuhl platzieren |
| `cash` | `id=0, table={table_id}, chair={chair_id}` | Fertigen Gast abkassieren (Gutschrift Basis + Tip + Bonus direkt in `menue.bar`) |
| `transfer` | `id=0, table=0, chair=0` | Manueller Geldtransfer (falls Kasse gepuffert wird) |

### 2.3 Python-Implementierung (`app/modules/foodworld/`)

Die Python-Implementierung von Phase 9 kapselt das Gastronomie-System vollständig und entkoppelt in Services, DTO-Modelle und REST-Routen:

#### 1. Modulaufbau & Klassen:
- **`app/modules/foodworld/models.py`**:
  - `KitchenBuilding` & `KitchenSlot`: Repräsentieren die 4 Küchengebäude (`DrinkBooth`, `SnackBooth`, `PastryShop`, `IceCreamParlour`) und deren Produktionsslots (`remain`, `pid`, `is_ready`).
  - `TableChair`: Tischstuhl-Status (`table_id`, `chair_id`, `farmi_id`, `remain`, `status`, `is_ready_to_cash`).
  - `FoodworldFarmi`: Wartender Restaurantgast mit Verzehrdauer (`duration`), geforderten Speisen (`products`), Basisvergütung, Trinkgeld und Restaurant-Bonus.
  - `FoodworldRecipe`: Rezeptdaten mit Zubereitungszeit (`time`) und Zutatenliste (`ingredients`).
  - `FoodworldSettings`: Laufzeitkonfiguration (`enabled`, `auto_cook`, `auto_seat`, `auto_cash`, `auto_unlock_tables`, `export_to_market`, `dish_reserve_buffer`, `only_empty_market`).
  - `FoodworldSummary`: Aggregierter Statusbericht für REST-API und Dashboard.
- **`app/modules/foodworld/kitchen.py` (`KitchenService`)**:
  - `pickup_products()`: Erntet alle fertigen Gerichte (`action: crop`) ab.
  - `produce(demanded_cart, reserve_buffer)`: Startet Kochvorgänge für freie Slots. Folgt strikter Priorisierung (Behebung BUG-001):
    1. **Priorität 1 (Farmi-Bedarfe):** Berechnet das Netto-Defizit $\text{demanded} - (\text{stock} + \text{in\_production})$. Deckt Bedarfe wartender Farmis (sortiert nach Ertrag/Preis). Bei hohem Bedarf werden mehrere Slots zugewiesen.
    2. **Priorität 2 (Pufferbestand):** Füllt Gerichte bis `dish_reserve_buffer` (50) auf, aber **nur wenn keine offenen Farmi-Defizite mehr existieren** (max. 1 Slot pro Bude).
    3. **Keine blinde Fallback-Produktion:** Unbenötigte Gerichte werden nicht gekocht, um Rohstoffe zu schonen.
- **`app/modules/foodworld/tables.py` (`TableService`)**:
  - `cash_tables()`: Kassiert fertige Gäste ab (`action: cash`). Der Erlös fließt direkt in das Barvermögen.
  - `seat_guests()`: Weist wartende Gäste freien Stühlen zu (`action: dropped`). Gäste werden nach Profitabilität sortiert (`price` absteigend). Es werden nur Gäste platziert, deren Speisen vollständig im Lager vorhanden sind.
  - `get_demanded_cart()`: Liefert die aggregierten Mengenkontingente `{pid: needed_quantity}` wartender Farmis.
  - *Wichtig zur Upstream-Adressierung:* Tische sind im Upstream 0-indiziert (`0..num_tables-1`), Stühle sind 1-indiziert (`1..chairs_per_table`).
- **`app/modules/foodworld/service.py` (`FoodworldService`)**:
  - Orchestriert den gesamten Zyklus: `foodworld_init` $\rightarrow$ `cash_tables` $\rightarrow$ `pickup_products` $\rightarrow$ `produce` (Farmi-Bedarfe) $\rightarrow$ `seat_guests` $\rightarrow$ `_export_surplus_dishes`.
- **`app/api/endpoints/foodworld.py`**:
  - REST-API für Live-Status (`GET /api/v1/foodworld`) und dynamische Konfiguration (`GET/PUT /api/v1/foodworld/settings`).

#### 2. Spezifische Geschäftsregeln & Schutzmechanismen:
- **Farmi-First-Produktion & Netto-Bedarfsdeckung (BUG-001):**
  Die Küchenproduktion kocht primär Speisen, die von wartenden Restaurantgästen nachgefragt werden. Mehrere freie Slots können für dasselbe Gericht verwendet werden, wenn das Netto-Defizit mehrere Batches erfordert.
- **Pufferbestand (`dish_reserve_buffer = 50`):**
  Von jedem hergestellten Gericht werden standardmäßig 50 Einheiten im Hauptlager reserviert, um Restaurantgäste und Quests verzögerungsfrei bedienen zu können. Puffer-Kochen wird erst aktiv, wenn alle Farmi-Defizite bedient sind.
- **Kein Tischkauf (`auto_unlock_tables = False`):**
  Es werden keine neuen Tische für kT oder Coins gekauft. Nur bereits freigeschaltete Tische werden bewirtschaftet.
- **Markt-Export nur bei absolutem Angebotsleerstand (`only_empty_market = True`):**
  Überschussmengen oberhalb des 50er Puffers werden nur dann auf den Marktplatz gestellt, wenn für das jeweilige Produkt aktuell noch kein einziges Verkaufsangebot auf dem Markt aktiv ist. Dadurch werden Unterbietungskriege vermieden und Maximalpreise erzielt.
  *Wichtig (Kategorie-Filterung):* Es werden ausschließlich echte Foodworld-Gerichte (Kategorie `'fw'` bzw. registrierte Rezept-Ausgaben) exportiert. Reguläre Feldfrüchte (z. B. Kohlrabi PID 153 mit Kategorie `'v'`) und Fabrik-/Tiererzeugnisse sind strikt vom Foodworld-Marktexport ausgeschlossen.
- **Level-Strategie (kT-zu-XP Hebel):**
  Da Restaurant-Farmis 0 Erfahrungspunkte geben, fließen die durch die 123.5%-Vergütung erwirtschafteten kT direkt in den Freikauf von Hauptquests (Millionen XP) und die Finanzierung von High-XP-Pflanzen auf den Farmen.

---


## 3. Modul Events (`events/DeliveryEvent.js`)

- **Funktion:** `async function handleDeliveryEvent()`
- **Konfiguration:** `new Conf({configName: 'DeliveryEvent'})`
- **Ablauf:**
  - Ruft `getEventData()` auf: `{mode: 'deliveryevent_init'}`.
  - Prüft, ob bereits eine Tour läuft (`tour.remain >= 0`).
  - Wenn keine Tour läuft und genügend Event-Punkte vorhanden sind: Startet Liefertour via `{mode: 'deliveryevent_starttour', spot: spot.id}`.

---

## 4. Helper-Module (`helpers/`)

### Windmühle (`helpers/Windmill.js`)
- **Exportiert:** `async function handleWindmill()`
- **Ablauf:**
  - `update()`: API-Call `{mode: 'windmillinit', city: 2}`.
  - Wenn Produkt fertig: `{mode: 'windmillcrop', city: 2, slot}`.
  - Wenn leer: Wählt eine zufällige Backform/Rezept (`formula`), beschafft Zutaten via `Stock.graspProducts(...)` und startet Mühlenproduktion via `{mode: 'windmillstartproduction', city: 2, slot, formula: formula.id}`.

### Boni & Tiere (`helpers/Bonus.js`)
- **Exportiert:** `async function handleBonus()`
- **Ablauf:**
  - Fragt Farmstatus ab: `{mode: 'getfarms', farm: 1, position: 0}`.
  - **Braver Ben (Hofhund):** Wenn `menue.farmdog_harvest` bereit ist: `{mode: 'dogbonus', farm: 1, position: 0}`.
  - **Goldesel Waltraud:** Wenn `menue.donkey === 1`: Ruft `{mode: 'dailydonkey', farm: 1, position: 1}` ab.

### Losbude / Lotterie (`helpers/Lottery.js`)
- Zieht das tägliche Gratis-Los an der Losbude via `{mode: 'lottery_init'}` und `{mode: 'lottery_draw'}`.
