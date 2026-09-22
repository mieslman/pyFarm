---
title: LLM-Wiki Structure and Workflow (OKF)
author: System
date: 2026-09-11
type: meta
description: Beschreibt den Aufbau, Ablauf und das Inhaltsverzeichnis des LLM-Wikis für MyFreeFarm.
tags: [wiki, meta, okf, index]
---
# LLM-Wiki für MyFreeFarm (OKF Standard)

Dieses Wiki ist im **Open Knowledge Format (OKF)** von Google strukturiert (auch bekannt als "LLM-Wiki nach Karpathy"). 
Es dient Entwicklern und KI-Agenten als strukturierte Wissensbasis zur Dokumentation des bestehenden Node.js-Quellcodes und zur Vorbereitung des vollständigen Redesigns in **Python**.

---

## Inhaltsverzeichnis der Dokumente

| Datei | Typ | Beschreibung |
| :--- | :--- | :--- |
| **[00-README.md](00-README.md)** | `meta` | Struktur, Konventionen und Workflow des Wikis. |
| **[01-architecture.md](01-architecture.md)** | `architecture` | High-Level Architektur (Node.js/Express, PouchDB, Module). |
| **[02-source-code-overview.md](02-source-code-overview.md)** | `code` | Einstiegspunkte (`index.js`, `mffserver.js`) & Hauptschleife. |
| **[03-module-agriculture.md](03-module-agriculture.md)** | `module` | Ackerbau (`Field`, `Farm`, `Vehicle`, `SushiBar`), Pflanzstrategien. |
| **[04-module-forestry.md](04-module-forestry.md)** | `module` | Forstwirtschaft (`Forrest`, `Sawmill`, `Carpentry`, `OrderManager`). |
| **[05-module-farm.md](05-module-farm.md)** | `module` | Gebäude-Management, Tierställe (`Shed`), Fabriken (`Factory`). |
| **[06-module-farmersmarket.md](06-module-farmersmarket.md)** | `module` | Gärtnerei (`Nursery`), Blumenbeete, Tierzucht (`PetBreed`). |
| **[07-module-stall.md](07-module-stall.md)** | `module` | Obststand / Marktbude (`Stall.js`), Slot-Management. |
| **[08-module-misc.md](08-module-misc.md)** | `module` | Insektenhotel, Foodworld, Liefer-Events, Windmühle, Tierboni. |
| **[09-core-services-stock.md](09-core-services-stock.md)** | `module` | Kernservices: `Stock`, `Market`, `SeedDealer`, `Trade`, `Contracts`, `Quests`. |
| **[10-express-routes-api.md](10-express-routes-api.md)** | `code` | Interne REST-API (`/api/v1/*`), JWT-Authentifizierung, Schemata. |
| **[11-external-game-api.md](11-external-game-api.md)** | `code` | Upstream-Protokoll zu `myfreefarm.de` (Token, RID, AJAX-Endpunkte). |
| **[12-python-redesign-spec.md](12-python-redesign-spec.md)** | `plan` | Python Zielarchitektur (FastAPI, HTTPX, Pydantic, ohne DB) & Migrationsplan. |
| **[13-python-models-and-algorithms.md](13-python-models-and-algorithms.md)** | `architecture` | Pydantic v2 Datenmodelle, Ackerbau Grid-Fitting, Futteroptimierung & Trading. |
| **[14-python-worker-and-scheduler.md](14-python-worker-and-scheduler.md)** | `architecture` | Single-Account 10-Min-Worker, Lifecycle, Upstream Session, Anti-Ban & Mocking. |
| **[15-python-fastapi-api-and-dashboard.md](15-python-fastapi-api-and-dashboard.md)** | `architecture` | FastAPI REST-API, JWT-Auth, WebSocket-Echtzeit-Streaming & Single-Page Dashboard. |
| **[16-python-extension-modules-plan.md](16-python-extension-modules-plan.md)** | `plan` | Detaillierter Phasenplan (Phasen 6–11) für verbleibende Module (Bauernmarkt, Obststand, Foodworld, etc.). |
| **[17-quest-system.md](17-quest-system.md)** | `module` | Questsystem: Hauptquestreihen 1–6 (709 Quests), Stammdatenkataloge (`help.php`), Live-Status & Kampagnen. |
| **[18-python-sushibar-module.md](18-python-sushibar-module.md)** | `module` | Sushi-Bar Modul (Farm 8, Hof-Gastronomie, Questreihe 5 Solver, Feldreserve & Coin-Schutz). |
| **[19-python-factories-module.md](19-python-factories-module.md)** | `module` | Veredelungsfabriken (Ölpresse, Käserei, Spinnerei, Strickerei, Marmeladenküche mit Quest-Vorrang). |
| **[20-module-spicehouse.md](20-module-spicehouse.md)** | `module` | Gewürzhaus & Gewürzmühlen (Farm 10, Trockenofen, kontinuierliche Mühlen, Kunden & Streuer-Währung). |
| **[21-module-season-events.md](21-module-season-events.md)** | `module` | Saisonevents & Saisonale Reise (Liefertouren, Kalender, Eventgarten, Oktoberfest-Solver & Seasonpass). |

---

## OKF Dokumenten-Konvention

Jede Datei folgt dem Standard aus YAML-Frontmatter und Markdown:
```yaml
---
title: Titel des Dokuments
author: Autor / System
date: YYYY-MM-DD
type: [architecture, module, code, meta, plan]
description: Zusammenfassung des Inhalts.
tags: [tag1, tag2]
---
```

## Workflow für Erweiterungen
1. **Neue Datei anlegen:** Name im Format `XX-thema.md` in `llm-wiki/` anlegen.
2. **Metadaten definieren:** YAML-Frontmatter mit treffenden `tags` und `type` ausfüllen.
3. **Kontext verlinken:** Immer Querverweise zu verwandten Dokumenten und konkreten Code-Dateien setzen.
4. **README aktualisieren:** Neuen Eintrag in der Tabelle oben ergänzen.
