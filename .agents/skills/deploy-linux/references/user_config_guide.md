# Referenz: Farm-Konfiguration (`user_config.json`)

Die Datei `data/user_config.json` steuert sämtliche Automationsregeln für die Farmen.

---

## 1. Wichtigste Sektionen

### `agriculture` (Ackerbau)
- `enabled` (boolean): Aktiviert/deaktiviert Ackerbau-Automation.
- `plant_strategy` (string):
  - `"plantQuest"`: Priorisiert das Anbauen von Pflanzen für aktive Quests.
  - `"fillStock"`: Füllt Lagerbestände bis zu `min_products` auf.
  - `"fixed"`: Pflanzt fest definierte Pflanze je Feld.
- `auto_water` (boolean): Gießt Felder automatisch bei Bedarf.
- `auto_crop` (boolean): Erntet reife Pflanzen automatisch ab.
- `harvest_mode` (string): `"all"` oder `"ready_only"`.
- `min_products` (integer): Minimalbestand im Lager.
- `farm_crops` (dict): Zuordnung Feld-ID -> Pflanzen-ID (`PID`).

### `trade` (Markt & Verkauf)
- `enabled` (boolean): Aktiviert den automatischen Marktverkauf.
- `min_credit_kt` (float): Minimaler Barbestand vor neuen Zukäufen.
- `underbid_offset_kt` (float): Cent-Betrag zur Marktpreis-Unterbietung.
- `default_reserve` (integer): Sicherheitsbestand, der nie verkauft wird.
- `auto_sell_surplus` (boolean): Überschüsse oberhalb des Buffers verkaufen.

### `stock` (Lager & Einkauf)
- `buffer_crops` (integer): Mindestmenge an Feldfrüchten im Lager (Standard: 500).
- `max_price_factor_crops` (float): Maximaler Preis-Multiplikator für Nachkäufe.
- `min_credit_kt` (float): Schutzgrenze für Kartoffeltaler.

### `forestry` (Baumerei & Forstwirtschaft)
- `enabled` (boolean): Aktiviert Forstwirtschaft.
- `auto_cut` (boolean): Fällt ausgewachsene Bäume.
- `auto_plant` (boolean): Pflanzt neue Bäume.
- `auto_water` (boolean): Bewässert Bäume.
- `auto_produce` (boolean): Bedient Sägewerk / Schreinerei.
- `serve_farmis` (boolean): Bedient Holzkunden an der Schranke.

### `helpers` (Tägliche Boni & Farm-Helfer)
- `farm_dog` (boolean): Täglicher Knochen für den Farmhund.
- `lottery` (boolean): Tägliches Glücksrad drehen.
- `donkey` (boolean): Esel streicheln/füttern.

---

## 2. Häufige Anpassungen über die CLI

```powershell
# Ackerbau auf Quest-Strategie stellen
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-set agriculture.plant_strategy "plantQuest" --push

# Automatisches Bewässern aktivieren
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-set agriculture.auto_water true --push

# Markt-Verkauf vorübergehend deaktivieren
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-set trade.enabled false --push

# Mindestreserve im Lager anpassen
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-set stock.buffer_crops 600 --push
```
