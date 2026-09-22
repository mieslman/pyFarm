# Buglist & Issue Tracker (MyFreeFarm Python)

> **Verwendungszweck für Automation & KI-Agenten:**  
> Diese Datei dient als strukturierter Issue-Tracker für bekannte Fehler und Optimierungen.  
> Jeder Eintrag besitzt eine eindeutige ID (`BUG-xxx`), einen standardisierten Status-Zyklus, maschinenlesbare Metadaten sowie eine Checkliste von Schritten und Akzeptanzkriterien zur automatisierten Bearbeitung und Fortschrittsverfolgung.

---

## Status-Übersicht & Metriken

| ID | Status | Priorität | Modul | Titel | Letzte Aktualisierung |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [BUG-001](#bug-001-foodworld-produzierte-produkte-passen-nicht-zu-farmi-anforderungen) | `RESOLVED` | `HIGH` | `foodworld` | Foodworld: Produzierte Produkte passen nicht zu Farmi-Anforderungen | 2026-09-18 |
| [BUG-002](#bug-002-farm-6-alpin-restliche-äcker-bleiben-leer-wenn-saatgut-der-primären-quest-pflanze-aufgebraucht-ist) | `OPEN` | `HIGH` | `agriculture` | Farm 6 (Alpin): Restliche Äcker bleiben leer, wenn Saatgut der primären Quest-Pflanze aufgebraucht ist | 2026-09-15 |
| [BUG-003](#bug-003-windmühle-start-der-produktion-schlägt-mit-rezept-0-fehl) | `RESOLVED` | `HIGH` | `helpers` | Windmühle: Start der Produktion schlägt mit 'Rezept 0' fehl (PHP-Serialisierung) | 2026-09-18 |

**Status-Legende:**
- `OPEN` (`[ ]`): Offen, bereit zur Bearbeitung.
- `IN_PROGRESS` (`[/]`): In aktiver Bearbeitung durch Agent/Entwickler.
- `RESOLVED` (`[x]`): Behoben, Tests erfolgreich, verifiziert.
- `BLOCKED` (`[-]`): Blockiert durch externe Abhängigkeiten oder fehlende Daten.

---

## Standardisiertes Eintrags-Schema

Für neue Einträge muss folgende Struktur eingehalten werden:

```markdown
### [BUG-xxx] <Titel>

- **Status**: OPEN | IN_PROGRESS | RESOLVED | BLOCKED
- **Priorität**: LOW | MEDIUM | HIGH | CRITICAL
- **Betroffenes Modul**: `<modul_name>`
- **Betroffene Dateien**: [`<datei>`](file:///path/to/file)
- **Erstellt am**: YYYY-MM-DD
- **Zugewiesen an**: Agent / Entwickler

#### 1. Problembeschreibung
<Genaue Beschreibung des Fehlverhaltens und der Diskrepanz zwischen Soll- und Ist-Zustand>

#### 2. Technische Ursachenanalyse
<Code-Stellen, Datenflüsse, Fehlannahmen>

#### 3. Akzeptanzkriterien (Definition of Done)
- [ ] Kriterium 1
- [ ] Kriterium 2

#### 4. Abarbeitungsschritte
- [ ] Schritt 1
- [ ] Schritt 2

#### 5. Verifikation
<Testbefehle und Prüfpunkte>
```

---

## Bug-Einträge

### [BUG-001] Foodworld: Produzierte Produkte passen nicht zu Farmi-Anforderungen

- **Status**: RESOLVED
- **Priorität**: HIGH
- **Betroffenes Modul**: `app.modules.foodworld`
- **Betroffene Dateien**:
  - [`app/modules/foodworld/service.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/foodworld/service.py)
  - [`app/modules/foodworld/kitchen.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/foodworld/kitchen.py)
  - [`app/modules/foodworld/tables.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/foodworld/tables.py)
  - [`tests/test_foodworld.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/tests/test_foodworld.py)
- **Erstellt am**: 2026-09-14
- **Zugewiesen an**: Agent

#### 1. Problembeschreibung
In der Foodworld (Picknick-Bereich / Restaurant) stimmten die in den Küchen produzierten Gerichte nicht mit den tatsächlichen Anforderungen der wartenden Farmis überein:
- Farmis fordern bestimmte Gerichte in konkreten Mengen über ihren Einkaufskorb (`farmi.cart`).
- Die Küchenzubereitung priorisierte unzureichend: Es wurden Gerichte zubereitet, die kein wartender Farmi verlangte (durch pauschale Reserve-Puffer oder wahllose Fallback-Produktion), während tatsächlich nachgefragte Speisen fehlten.
- Dies führte dazu, dass Küchenslots mit unbenötigten Gerichten blockiert wurden, wartende Farmis nicht platziert bzw. bedient werden konnten und unnötig Rohstoffe gebunden wurden.

#### 2. Technische Ursachenanalyse
1. **Unvollständige Bedarfsermittlung in [`TableService.get_demanded_pids()`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/foodworld/tables.py#L202-L211):**
   - Die Methode extrahierte lediglich eine flache Liste eindeutiger PIDs (`list[int]`), ignorierte jedoch die exakte geforderte Anzahl pro Farmi sowie die Priorität der Gäste (z. B. nach Ertrag `price` oder Wartezeit).
2. **Fehlende Soll/Ist-Bedarfsaggregation:**
   - Vor dem Kochen wurde nicht ermittelt:
     $$\text{Offener Bedarf} = \text{Bedarf aller wartenden Farmis} - (\text{Lagerbestand} + \text{bereits im Kochslot befindliche Gerichte})$$
3. **Aggressive Fallback-Produktion in [`KitchenService.produce()`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/foodworld/kitchen.py#L182-L202):**
   - Priorität 2 füllte alle Rezepte unter `reserve_buffer` auf.
   - Priorität 3 startete die Zubereitung beliebiger Gerichte, sobald Zutaten vorhanden waren, selbst wenn null Nachfrage bestand.
4. **Restriktiver `active_pids`-Filter:**
   - Ein Gericht wurde pro Küche nur einmal aktiv angesetzt, selbst wenn mehrere wartende Farmis oder hohe Stückzahlen desselben Gerichts verlangt wurden.

#### 3. Akzeptanzkriterien (Definition of Done)
- [x] Die Bedarfsermittlung (`get_demanded_cart()`) liefert ein Mengenkontingent `{pid: needed_quantity}`, aggregiert über alle wartenden Farmis (`status == 0`), priorisiert nach Ertrag/Preis.
- [x] Die Produktion berücksichtigt den aktuellen Lagerbestand (`stock_service.get_amount(pid)`) und bereits laufende Kochslots (`building.slots`), um Überproduktion zu vermeiden.
- [x] Küchenslots werden primär mit Speisen belegt, für die ein aktiver Farmi-Bedarf besteht.
- [x] Fallback- bzw. Pufferproduktion wird nur dann ausgeführt, wenn keine ungedeckten Farmi-Bedarfe existieren oder konfigurierbar steuerbar ist.
- [x] Wenn mehrere Farmis dasselbe Gericht fordern, können bei Bedarf mehrere Slots für dieses Gericht genutzt werden.
- [x] Automatisierte Tests in `tests/test_foodworld.py` bestätigen, dass bei Farmi-Nachfrage nach PID X genau PID X gekocht wird und keine unbenötigten PIDs Y/Z blockieren.

#### 4. Abarbeitungsschritte
- [x] 1. **Bedarfslogik in `tables.py` anpassen**: Methode `get_demanded_cart() -> dict[int, int]` implementiert, die den Gesamtbedarf wartender Farmis aggregiert.
- [x] 2. **Produktionslogik in `kitchen.py` anpassen**: `produce()` umgebaut, sodass der Netto-Fehlbestand berechnet und priorisiert gekocht wird.
- [x] 3. **Konfiguration prüfen**: Parameter wie `dish_reserve_buffer` und Farmi-First-Modus abgestimmt (blinde Fallback-Produktion entfernt).
- [x] 4. **Unit- & Integrationstests schreiben**: Tests in `tests/test_foodworld.py` um Szenarien mit Farmi-Bedarfen, Mengendeckung und Slot-Zuteilung erweitert.
- [x] 5. **Regressionstests durchführen**: Gesamte Test-Suite `pytest -m "not live"` fehlerfrei ausgeführt (101/101 passed).
- [x] 6. **Dokumentation & Status-Update**: Status in dieser Buglist auf `RESOLVED` gesetzt.

#### 5. Verifikation
```bash
pytest tests/test_foodworld.py -v
```
Prüfung in den Logs:
- `Foodworld-Küche: Starte Zubereitung von '<Farmi-Wunsch>'...`
- Keine Produktion von ungefragten Gerichten, solange wartende Farmis offene Bedarfe haben.

---

### [BUG-002] Farm 6 (Alpin): Restliche Äcker bleiben leer, wenn Saatgut der primären Quest-Pflanze aufgebraucht ist

- **Status**: OPEN
- **Priorität**: HIGH
- **Betroffenes Modul**: `app.modules.agriculture`
- **Betroffene Dateien**:
  - [`app/modules/agriculture/field.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/agriculture/field.py)
  - [`app/modules/agriculture/strategies.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/agriculture/strategies.py)
  - [`app/modules/farm_buildings/farm_service.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/farm_buildings/farm_service.py)
  - [`tests/test_agriculture.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/tests/test_agriculture.py)
- **Erstellt am**: 2026-09-15
- **Zugewiesen an**: Unassigned

#### 1. Problembeschreibung
Auf Farm 6 (Alpin-Farm) werden nicht alle Äcker bepflanzt:
- Eine aktive Quest benötigt zwei Pflanzenarten (z. B. Malve und Melisse).
- Der erste Acker wird mit Malve bepflanzt, wodurch das verfügbare Saatgut für Malve im Alpin-Regal aufgebraucht ist.
- Für die nachfolgenden Äcker (z. B. Feld 6/2, 6/3, 6/4) wählt die Strategie `plantQuest` weiterhin 'Malve' als Kandidaten aus, anstatt auf die ebenfalls benötigte 'Melisse' (für die Saatgut vorhanden ist) auszuweichen.
- Der Sävorgang für Malve schlägt fehl (Serverrückmeldung: `In deinem Acker ist kein Platz mehr` bzw. keine Samen verfügbar) und die restlichen Äcker bleiben komplett leer.

**Auszug aus den Logs:**
```text
[17:23:30] INFO app.modules.agriculture.field:169 Feld 6/2: Säe 'Malve' (PID 705) auf 120 freie Kacheln...
[17:23:31] WARNING app.modules.agriculture.field:185 Feld 6/2: Säen von 'Malve' fehlgeschlagen: In deinem Acker ist kein Platz mehr.
[17:23:31] INFO app.modules.agriculture.strategies:149 Strategie 'plantQuest' (alpin): Gewählte Pflanze 'Malve' (PID 705, Bestand: 5591/500)
[17:23:31] INFO app.modules.agriculture.field:215 --- Feld 6/3 (Acker) wird bedient ---
[17:23:32] INFO app.modules.agriculture.field:154 Feld 6/3: Ernte 120 reife Pflanzen (all)...
[17:23:34] INFO app.modules.agriculture.field:169 Feld 6/3: Säe 'Malve' (PID 705) auf 120 freie Kacheln...
[17:23:35] WARNING app.modules.agriculture.field:185 Feld 6/3: Säen von 'Malve' fehlgeschlagen: In deinem Acker ist kein Platz mehr.
[17:23:35] INFO app.modules.agriculture.strategies:149 Strategie 'plantQuest' (alpin): Gewählte Pflanze 'Malve' (PID 705, Bestand: 5591/500)
[17:23:35] INFO app.modules.agriculture.field:215 --- Feld 6/4 (Acker) wird bedient ---
```

#### 2. Technische Ursachenanalyse
1. **Kein Bestandsabgleich nach Sävorgängen:**
   - Nach erfolgreichem Säen (`field.plant()`) wird der Antwort-Body mit `updateblock.stock` nicht an [`StockService.update()`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/services/stock_service.py#L51-L100) weitergegeben.
   - Der lokale Regalbestand (`stock_service.get_farm_amount(farm_id, pid)`) bleibt innerhalb desselben Zyklus unverändert.
2. **Fehlender Ausschluss von Pflanzen ohne Saatgut in `PlantStrategySolver`:**
   - In [`PlantStrategySolver.get_plant_quest_candidates()`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/agriculture/strategies.py#L36-L74) werden Kandidaten nur nach `total_amount < required_amount + min_products` gefiltert und nach `get_farm_amount(farm_id, p.pid) > 0` sortiert.
   - Hat ein Produkt 0 Saatgut im lokalen Farm-Regal, wird es nicht aus der Auswahlliste verworfen, solange ein Quest-Defizit besteht. Ist der Regalbestand veraltet oder stehen keine Samen zur Verfügung, wird dennoch Malve gewählt.
3. **Keine Fallback-Kandidaten bei Fehlschlag:**
   - [`PlantStrategySolver.resolve_candidate()`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/agriculture/strategies.py#L77-L167) gibt lediglich eine einzelne Pflanze zurück.
   - Schlägt `field.plant()` fehl, existiert in [`Field.serve()`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/agriculture/field.py#L213-L225) kein Fallback auf den nächsten Kandidaten der Strategie (z. B. Melisse).

#### 3. Akzeptanzkriterien (Definition of Done)
- [ ] [`StockService`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/services/stock_service.py) wird nach Sävorgängen über die Server-Rückgabe (`updateblock.stock`) aktualisiert bzw. der lokale Regalbestand wird dekrementiert.
- [ ] [`PlantStrategySolver.get_plant_quest_candidates()`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/agriculture/strategies.py#L36-L74) wählt strikt nur Pflanzen, für die auf der betreffenden Farm Saatgut vorhanden ist (`farm_amount > 0`), sofern alternative Quest-Pflanzen Saatgut besitzen.
- [ ] Schlägt das Säen einer Pflanze fehl, versucht [`Field.serve()`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/agriculture/field.py#L213-L225) bzw. die Steuerung den nächsten verfügbaren Kandidaten mit Saatgut zu pflanzen.
- [ ] Alle freien Kacheln auf Farm 6 (und anderen Farmen) werden bepflanzt, solange mindestens eine zulässige Quest- oder Min-Stock-Pflanze mit vorhandenem Saatgut existiert.
- [ ] Unit-Tests in `tests/test_agriculture.py` prüfen das Verhalten bei mehreren Äckern, wenn Saatgut für Pflanze 1 aufgebraucht wird und auf Pflanze 2 ausgewichen werden muss.

#### 4. Abarbeitungsschritte
- [ ] 1. **Bestandsaktualisierung nach Aktionen**: Rückmeldungen von `field.plant()` und `field.crop()` an `stock_service.update()` weiterleiten.
- [ ] 2. **Kandidatenfilterung in `strategies.py` anpassen**: `get_plant_quest_candidates()` so filtern bzw. priorisieren, dass Pflanzen ohne lokales Saatgut (`farm_amount == 0`) übersprungen werden, wenn alternative Quest-Pflanzen mit Saatgut existieren.
- [ ] 3. **Fallback-Säen implementieren**: `Field.serve()` / `FarmService` ermöglichen, bei Fehlschlag eines Sävorgangs alternative Kandidaten zu säen.
- [ ] 4. **Unit-Tests implementieren**: In `tests/test_agriculture.py` Testfall für sequenzielles Bepflanzen mit erschöpftem Erstkandidaten hinzufügen.
- [ ] 5. **Regressionstests durchführen**: Testsuite `pytest tests/test_agriculture.py` erfolgreich ausführen.
- [ ] 6. **Dokumentation & Status-Update**: Status in `buglist.md` nach Behebung auf `RESOLVED` setzen.

#### 5. Verifikation
```bash
pytest tests/test_agriculture.py -v
```
Prüfung im Live-Betrieb / Mock:
- Feld 6/1 sät 'Malve'.
- Feld 6/2 erkennt, dass Malven-Saatgut erschöpft ist, und sät 'Melisse'.
- Keine Äcker bleiben unbepflanzt, solange alternatives Saatgut vorhanden ist.

---

### [BUG-003] Windmühle: Start der Produktion schlägt mit 'Rezept 0' fehl

- **Status**: RESOLVED
- **Priorität**: HIGH
- **Betroffenes Modul**: `app.modules.helpers`
- **Betroffene Dateien**:
  - [`app/modules/helpers/helpers_service.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/helpers/helpers_service.py)
  - [`tests/test_helpers.py`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/tests/test_helpers.py)
- **Erstellt am**: 2026-09-18
- **Zugewiesen an**: Agent

#### 1. Problembeschreibung
In der Windmühle (Dorf 2) schlägt das automatische Ansetzen einer Backproduktion fehl. Die Engine versucht kontinuierlich, ein ungültiges `'Rezept 0'` in Slot 1 zu starten, was vom Spielserver abgelehnt wird:

**Auszug aus den Logs:**
```text
[14:42:32] INFO app.modules.helpers.helpers_service:202 Helpers: Starte Windmühlen-Produktion: 'Rezept 0' in Slot 1...
[14:42:33] WARNING app.modules.helpers.helpers_service:220 Helpers: Starten der Windmühle fehlgeschlagen: {'updateblock': {...}, 'datablock': [0, 'Es ist ein Fehler aufgetreten']}
```

#### 2. Technische Ursachenanalyse
1. **PHP-JSON-Serialisierung von Rezept-Objekten:**
   - In der Antwort von `mode=windmillinit&city=2` ist `datablock[1]` ein Wörterbuch verfügbarer Rezepte.
   - Jedes Rezept ist im PHP-Backend ein Array mit nummerierten Feldern (`0`: ID, `1`: Typ/Level, `2`: Name, `3`: Zutatenliste) sowie einem benannten Attribut `amount`.
   - Da ein benanntes Feld (`amount`) existiert, encodiert PHP (`json_encode`) das Array als JSON-Objekt (`dict`) mit Zeichenketten-Schlüsseln: `{"0": 101, "1": 1, "2": "Weißbrot", "3": [[1, 10]], "amount": 1}` anstelle einer JSON-Liste (`list`).
2. **Fehlendes Key-Mapping in `WindmillFormula`:**
   - [`WindmillFormula.__init__`](file:///c:/Projekte/MyFreeFarm/myfreefarm/myfreefarm_python/app/modules/helpers/helpers_service.py#L12-L46) prüfte lediglich `if isinstance(raw, list):`.
   - Im `else`-Zweig für `dict` wurde nach den Schlüsseln `"id"`, `"name"` und `"requirements"` gesucht (`raw.get("id", 0)`), welche im Upstream-JSON nicht existieren.
   - Folglich initialisierte sich jedes Rezept mit `id = 0`, `name = "Rezept 0"` und `requirements = []`.
3. **Leere Zutatenprüfung & Schleifenüberlauf:**
   - `StockService.grasp_products([])` lieferte für leere Anforderungen sofort `True`.
   - Danach rief `helpers_service` die API mit `{"mode": "windmillstartproduction", "city": 2, "slot": 1, "formula": 0}` auf, was vom Server mit `datablock: [0, 'Es ist ein Fehler aufgetreten']` abgelehnt wurde.
   - Da kein `return True` erfolgte, wiederholte die Schleife diesen fehlerhaften Aufruf für alle im Dictionary vorhandenen Rezepte.

#### 3. Akzeptanzkriterien (Definition of Done)
- [x] `WindmillFormula` parst Upstream-Rezepte sowohl als Listen (`list`) als auch als Wörterbücher (`dict`) mit numerischen String-Schlüsseln (`"0"`, `"1"`, `"2"`, `"3"`, `"amount"`).
- [x] Rezepte mit ungültiger `id <= 0` oder leeren Zutatenanforderungen (`requirements == []`) werden vom Produktionsversuch strikt ausgeschlossen.
- [x] Ein automatisierter Test in `tests/test_helpers.py` verifiziert das korrekte Parsen von Upstream-PHP-Wörterbüchern und stellt sicher, dass die korrekte `formula`-ID an den Server übermittelt wird.
- [x] Keine Fehlversuche mit `formula: 0` mehr in den Live-Logs.

#### 4. Abarbeitungsschritte
- [x] 1. **`WindmillFormula` erweitern**: Unterstützung für String-Schlüssel `"0"`, `"2"`, `"3"`, `"amount"` sowie Fallback auf `"id"`, `"name"`, `"requirements"` implementiert.
- [x] 2. **Validierung im Produktionsloop hinzufügen**: Prüfen auf `formula.id > 0` und `bool(formula.requirements)` vor `grasp_products`.
- [x] 3. **Unit-Test schreiben**: `test_windmill_php_object_formulas` in `tests/test_helpers.py` hinzugefügt.
- [x] 4. **Regressionstests durchführen**: Testsuite `pytest tests/test_helpers.py` (5/5) und Gesamttests (97/97) erfolgreich ausgeführt.
- [x] 5. **Dokumentation & Status-Update**: Status in `buglist.md` auf `RESOLVED` gesetzt.

#### 5. Verifikation
```bash
uv run pytest tests/test_helpers.py -v
```
Prüfung im Live-Betrieb:
- Windmühlen-Logs zeigen den echten Rezeptnamen (z. B. `Weißbrot`, `Baguette`) und die korrekte ID `formula > 0`.
- Keine Warnungen `Starten der Windmühle fehlgeschlagen: [0, 'Es ist ein Fehler aufgetreten']` mehr durch ungültige Formula-0-Aufrufe.


