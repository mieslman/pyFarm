---
title: Core Services & Lagerhaltung (Stock, Trade, Contracts, Quests)
author: System
date: 2026-09-11
type: module
description: Detaillierte Implementierung von Lagerverwaltung (Stock), Marktkauf/Verkauf, Saatguthändler, Verträgen und Quests.
tags: [module, core, stock, market, trade, contracts, quests, interfaces]
---
# Core Services & Lagerhaltung

Die Core Services bilden das informationelle und wirtschaftliche Rückgrat der Anwendung.

## 1. Modul Stock (`stock/Stock.js`)

`Stock` ist ein Singleton, das alle bekannten Spielprodukte und die aktuellen Lagerbestände des Spielers verwaltet.

### Attribute
- `products`: Array von Produkt-Objekten indiziert nach `pid`:
  ```javascript
  {
    pid: string,            // Produkt-ID
    name: string,           // Name aus JS-Konstanten
    price: number,          // Standardpreis
    x: number, y: number,   // Feldgröße (z.B. 1x1, 2x2)
    category: string,       // z.B. 'v' (Pflanzen), 'e' (Erzeugnisse), 'ex' (Exoten)
    amount: number,         // Im Lagerregal
    tmpAmount: number       // Im Zwischenlager / Kiste
  }
  ```
- `market`: Instanz von `Market` (Marktplatz-Kauf)
- `seedDealer`: Instanz von `SeedDealer` (Saatguthändler-Kauf)

### Wichtige Methoden
- `async init(body)`:
  - Lädt Produktnamen und Preise aus dem Login-HTML (`var produkt_name`, `var produkt_price`).
  - Lädt Dimensionen und Kategorien aus `http://s{server}.myfreefarm.de/js/jsconstants_241014.js`.
  - Ruft `update()` auf, um Anfangsbestände einzulesen.
- `async update(body)`:
  - Aktualisiert `product.amount` aus `body.updateblock.stock.stock[1]` (Hauptlager/Regale).
  - Aktualisiert `product.tmpAmount` aus `body.updateblock.stock.tempstock`.
  - Parsed die lokalen Farm-Regale aller Farmen (`body.updateblock.stock.stock[farm_id]`) in `farm_stocks: dict[int, dict[int, int]]` (z. B. Farm 5 für Exoten, 6 für Alpin, 8 für Wasser, 10 für Gewürze).
- `get_farm_amount(farm_id, pid)`: Liefert die Anzahl Einheiten eines Produkts im spezifischen Regal der jeweiligen Farm (oder 0, falls nicht vorhanden).
- `getProduct(name)`: Sucht ein Produkt über seinen Klarnamen.
- `async graspProducts(products)`:
  - Zentrales Interface für alle Module zur Rohstoffbeschaffung.
  - Prüft für jedes Produkt `product.amount`. Fehlen Einheiten, wird automatisch auf dem Markt (`market.buy()`) oder beim Saatguthändler (`seedDealer.buy()`) nachgekauft.
  - Berücksichtigt Sicherheitsreserven (`suppl = 500` bei Pflanzen, sonst `5`).

---

## 2. Marktplatz & Saatgut (`stock/Market.js` & `stock/SeedDealer.js`)

- **`Market.buy({pid, amount, price})`**:
  - Ruft `{mode: 'marketinit', id: 0, comp: 1}` auf.
  - Filtert Angebote nach `offer.p === pid && offer.pr <= maxPrice`.
  - Führt Kauf aus via: `RestApi.apiCall('city', {mode: 'marketbuy', id: offer.id, comp: 1})`.
- **`SeedDealer.buy({pid, amount, price})`**:
  - Kauft direkt im Dorfladen: `RestApi.apiCall('city', {mode: 'shopfire', shopid: 1, cart: `${pid},${amount}`})`.

---

## 3. Handel & Verträge (`services/TradeService.js` & `services/ContractService.js`)

### `TradeService.js`
- Verkauft automatisch überschüssige Bestände am Markt:
  - Liest Konfiguration `Config.get('trade').sell`.
  - Wenn `amount - min >= config.amount`:
    - Erstellt Marktangebot: `{mode: 'marketcreateoffer', pid, amount: toSell, price, comp: 1}`.
  - Beachtet das definierte Minimum-Guthaben (`mincredit`).

### `ContractService.js`
- Erfüllt automatisch Verträge mit anderen Spielern:
  - Liest Konfiguration `Config.get('contract')`.
  - Wenn genug Ware vorhanden ist (`amount - min >= contract.amount`):
    - Sendet Vertrag: `RestApi.apiCall('main', {action: 'contracts_send', name: receiver, cart: `${pid}_${toDeal}_${price}_0|`, opt1: receiver})`.

---

## 4. Questsystem & Questreihen (`services/QuestService.js`)

Das Questsystem steuert das Vorantreiben der Haupt- und Außenfarmquests und dient dem Ackerbau-Modul (`plantQuest`) als primäre Bedarfsvorgabe.

### Die 6 Hauptquestreihen (709 Quests insgesamt):
- **Hauptquestreihe 1 (170 Quests):** Standardfarmen 1–4, Bauernhaus, Freischaltung von Farm 2 & 3, Gießbonus.
- **Hauptquestreihe 2 (120 Quests):** Fortgeschrittene Erzeugnisse & Tierprodukte.
- **Hauptquestreihe 3 (120 Quests):** Exoten-Reihe (Farm 5, Ananas & Exotensaatgut).
- **Hauptquestreihe 4 (99 Quests):** Naturschutzquest / Alpen-Reihe (Farm 6, Bergpflanzen wie Melisse, Enzian).
- **Hauptquestreihe 5 (100 Quests):** Wasserschutzquest / Teich-Reihe (Farm 8, Wasserpflanzen wie Wasserspinat, Reis).
- **Hauptquestreihe 6 (100 Quests):** Gourmetküchequest / Gewürz-Reihe (Farm 10, Gewürze wie Pfeffer, Zimt, Gewürzhaus).

### Schnittstellen:
- **Katalog aller Quests:** `help.php?mode=quests{1..6}` liefert den vollständigen statischen Katalog aller Quests inklusive PIDs, Mengen und Belohnungen.
- **Live-Zustand des Accounts:** `farm.php?mode=getfarms` liefert `updateblock.queststatus.main` mit den aktiven Quest-IDs und offenen Bedarfen.
- Ausführliche Dokumentation siehe: [**17-quest-system.md**](17-quest-system.md).

---

## 5. Python-Redesign (`StockService`)

- In Python wird der zentrale **`StockService`** als Singleton via Dependency Injection bereitgestellt.
- **Lokale Farm-Regale:** 
  - Neben dem globalen `products: dict[int, Product]` (basierend auf Hauptlager 1) führt `StockService` das Dictionary `farm_stocks: dict[int, dict[int, int]]`.
  - Bei jedem `update(updateblock)` werden die Regale aller gemeldeten Farmen strukturiert eingelesen.
  - Mit `get_farm_amount(farm_id: int, pid: int) -> int` fragen Acker-Module vor dem Bepflanzen ab, ob Saatgut im lokalen Regal der Ziel-Farm vorhanden ist.
- Thread- und Concurrency-Sicherheit (z.B. `asyncio.Lock` beim Ändern von Beständen oder Kaufen).
- Saubere Separation von Datenbeschaffung (Game AJAX API) und Business Rules (Wann wird gekauft? Welcher Preis ist akzeptabel?).

