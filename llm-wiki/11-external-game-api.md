---
title: Externe Spielserver-Schnittstelle (MyFreeFarm Protocol)
author: System
date: 2026-09-11
type: code
description: Vollständige Spezifikation des Upstream-HTTP-Protokolls zu den myfreefarm.de Spielservern.
tags: [api, protocol, upstream, http, authentication, interfaces]
---
# Externe Spielserver-Schnittstelle (MyFreeFarm Game Protocol)

Dieses Dokument spezifiziert das proprietäre HTTP/AJAX-Protokoll, über das die Anwendung mit den Spielservern von Upjers (`myfreefarm.de`) kommuniziert.

## 1. Authentifizierungs-Flow & Session Lifecycle

### Schritt 1: Token anfordern
- **Methode:** `POST`
- **URL:** `https://www.myfreefarm.de/ajax/createtoken2.php?n={timestamp_seconds}`
- **Content-Type:** `application/x-www-form-urlencoded`
- **Payload:**
  - `server`: Server-Nummer (z.B. `"21"`)
  - `username`: Account-Name
  - `password`: Account-Passwort
  - `ref`: `"frbek"`
  - `retid`: `""`
- **Response:** JSON-Array, z.B. `[1, "https://s21.myfreefarm.de/login.php?token=..."]`

### Schritt 2: Session initialisieren & RID extrahieren
- **Methode:** `GET` auf die in Schritt 1 erhaltene URL (`res.body[1]`).
- **Optionen:** `followRedirect = true`, persistente Cookie-Jar.
- **HTML-Parsing:** Aus dem empfangenen HTML-Body wird die Session-ID (`rid`) mittels Regex / String-Parsing extrahiert:
  ```javascript
  var rid = '0123456789abcdef...';
  ```

### Schritt 3: Statische Spieldaten nachladen
- **URL:** `http://s{server}.myfreefarm.de/js/jsconstants_241014.js`
- Enthält globale JavaScript-Objekte:
  - `var produkt_name = { ... };`
  - `var produkt_price = { ... };`
  - `var produkt_x = { ... };`, `var produkt_y = { ... };`
  - `var produkt_category = { ... };`

### Schritt 4: Logout
- **Methode:** `GET`
- **URL:** `http://s{server}.myfreefarm.de/main.php?page=logout&logoutbutton=1`

---

## 2. Das AJAX-Aufruf-Pattern (`RestApi.apiCall`)

Jeder Spielaufruf erfolgt als HTTP-GET an ein PHP-Skript auf der Subdomain des jeweiligen Spielservers:
```
https://s{server}.myfreefarm.de/ajax/{service}.php?rid={rid}&{params}
```

### Response-Format
Die Spielserver antworten standardmäßig im JSON-Format mit folgender Grundstruktur:
```json
{
  "status": "ok",
  "datablock": [
    1,
    { "... spezifische Antwortdaten ..." }
  ],
  "updateblock": {
    "stock": { "... aktueller Lagerstand ..." },
    "farms": { "... aktueller Farmstatus ..." },
    "menue": { "bar": "1234.56", "...": "..." }
  }
}
```
*Wichtig:* Nach nahezu jeder Aktion liefert der Spieleserver im `updateblock` den neuen Zustand der betroffenen Spielbereiche mit. Das JavaScript-Backend nutzt dies, um `Stock.update(body)` aufzurufen, ohne separate Statusabfragen machen zu müssen.

---

## 3. Zentrale Game Services & Parameterübersicht

### Service: `farm.php`
- `mode=getfarms&farm=1&position=0`: Holt Gesamtübersicht aller Farmen, Felder, Quests und Fahrzeuge.
- `mode=gardeninit&farm={f}&position={p}`: Lädt 120 Felder eines Ackers.
- `mode=autoplant&farm={f}&position={p}&id={pid}&product={pid}`: Pflanzt Acker voll. Greift auf das farm-spezifische Regal `stock[farm]` zu. Bei 0 Saatgut im lokalen Regal liefert der Server `[0, "In deinem Acker ist kein Platz mehr."]`.
- `mode=watergarden&farm={f}&position={p}`: Bewässert den Acker.
- `mode=cropgarden&farm={f}&position={p}`: Erntet reife Pflanzen.
- `mode=inner_init&farm={f}&position={p}`: Öffnet Fabrik oder Tierstall.
- `mode=inner_crop&farm={f}&position={p}[&slot={s}]`: Erntet Tier- oder Fabrikerzeugnis.
- `mode=inner_feed&farm={f}&position={p}&pid={pid}&amount={amt}`: Füttert Tiere.
- `mode=inner_init_production&farm={f}&position={p}&pid={pid}&amount={amt}&slot={s}`: Startet Fabrikrezept.
- `mode=flowerarea_harvest_all&farm=1&position=1`: Erntet alle reifen Blumenbeete auf der Blumenwiese auf einmal.
- `mode=flowerarea_autoplant&farm=1&position=1&set=0&pid={pid}`: Bepflanzt alle freien Beete der Blumenwiese vollständig mit Blumensamen `{pid}`.
- `mode=flowerarea_water_all&farm=1&position=1`: Bewässert alle bepflanzten Beete der Blumenwiese.
- `mode=nursery_harvest&farm=1&position=1&id={slot}&slot={slot}`: Erntet fertiges Gesteck aus Gärtnerei-Slot.
- `mode=nursery_startproduction&farm=1&position=1&id={pid}&pid={pid}&slot={slot}`: Startet Gesteckproduktion.
- `mode=flowerslot_remove&farm=1&position=1&set={slot}:1`: Räumt verwelktes Schau-Gesteck ab.
- `mode=flowerslot_water&farm=1&position=1&set={slot}:1`: Gießt Schau-Gesteck.
- `mode=flowerslot_plant&farm=1&position=1&set=1:{pid}`: Stellt neues Gesteck in Schau-Slot aus.
- `mode=handleflowerfarmi&farm=1&position=1&id={id}&farmi={id}&status=1`: Bedient Farmi am Marktstand.
- `mode=sushibar_init`, `sushibar_crop`, `sushibar_startproduction`, `sushibar_loadtrain`, `sushibar_servefarmi`: Sushibar.
- `mode=stall_init`, `stall_clear_slot`, `stall_fill_slot`, `stall_get_reward`: Obststand.
- `mode=insecthotel_init`, `insecthotel_set_stockslot`, `insecthotel_collect_checkout`: Insektenhotel.
- `mode=dogbonus`, `dailydonkey`: Tägliche Tierboni.

### Service: `forestry.php`
- `action=initforestry`: Initialisiert Baumerei.
- `action=cropall`: Fällt alle reifen Bäume.
- `action=autoplant&productid={pid}`: Bepflanzt Wald neu.
- `action=water`: Bewässert Bäume.
- `action=cropproduction&position={pos}&slot={s}`: Erntet Sägewerk/Schreinerei.
- `action=startproduction&position={pos}&slot={s}&productid={pid}`: Startet Holzproduktion.

### Service: `foodworld.php`
- `action=foodworld_init&id=0&table=0&chair=0`: Lädt gesamten Restaurant-Zustand (Küchen, Slots, Tische, Gäste, Rezepte).
- `action=crop&id=0&table={building_id}&chair={slot_id}`: Fertige Speisen aus Küchenslot abholen.
- `action=production&id={product_id}&table={building_id}&chair={slot_id}`: Speisenzubereitung in Küchenslot starten.
- `action=dropped&id={farmi_id}&table={table_id}&chair={chair_id}`: Wartenden Gast an freien Stuhl platzieren.
- `action=cash&id=0&table={table_id}&chair={chair_id}`: Rechnung kassieren (Gutschrift Basis + Tip + Bonus direkt auf kT-Konto).
- `action=quest_send&quest_id={id}`: Foodworld-Quest beliefern.

### Service: `city.php`
- `mode=marketinit&id=0&comp=1`: Lädt Marktplatz-Angebote.
- `mode=marketbuy&id={offer_id}&comp=1`: Kauft Angebot am Markt.
- `mode=marketcreateoffer&pid={pid}&amount={amt}&price={pr}&comp=1`: Stellt Ware zum Verkauf.
- `mode=shopfire&shopid=1&cart={pid},{amt}`: Kauft Ware beim Saatguthändler.
- `mode=windmillinit&city=2`, `windmillcrop`, `windmillstartproduction`: Mühle.

### Service: `main.php`
- `action=contracts_send&name={user}&cart={cart}&opt1={user}`: Sendet Vertrag an Spieler.

### Service: `quest.php`
- `action=init&campaign={c}&farm={f}`: Fragt den Status einer Quest-Kampagne (1 bis 6) für eine Farm ab.
- `action=sendproduct&campaign={c}&pid={pid}&amount={amt}`: Liefert benötigte Waren für die aktive Quest ab.
- `action=finishquest&campaign={c}`: Schließt die fertige Quest ab und verbucht die Belohnung.

### Service: `help.php`
- `mode=quests{1..6}`: Liefert den vollständigen statischen Katalog aller Quests der jeweiligen Hauptquestreihe (1=170 Q, 2=120 Q, 3=120 Q, 4=99 Q, 5=100 Q, 6=100 Q; insgesamt 709 Quests) als strukturierte HTML-Tabelle mit Mengenangaben, PIDs und Belohnungen.

---

## 4. Empfehlungen für den Python Client (`HTTPX`)

```python
import httpx
import re

class MFFGameClient:
    def __init__(self, server: int):
        self.server = server
        self.base_url = f"https://s{server}.myfreefarm.de"
        self.client = httpx.AsyncClient(cookies=httpx.Cookies(), follow_redirects=True, timeout=30.0)
        self.rid: str | None = None

    async def login(self, username: str, password: str) -> bool:
        # Step 1: Token
        resp = await self.client.post(
            f"https://www.myfreefarm.de/ajax/createtoken2.php?n={int(time.time())}",
            data={"server": self.server, "username": username, "password": password, "ref": "frbek", "retid": ""}
        )
        token_url = resp.json()[1]
        
        # Step 2: Session & RID
        login_resp = await self.client.get(token_url)
        match = re.search(r"var rid = '([a-f0-9]+)';", login_resp.text)
        if match:
            self.rid = match.group(1)
            return True
        return False

    async def api_call(self, service: str, params: dict) -> dict:
        params["rid"] = self.rid
        resp = await self.client.get(f"{self.base_url}/ajax/{service}.php", params=params)
        return resp.json()
```
