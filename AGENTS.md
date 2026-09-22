# Agent Guidelines & Project Instructions (MyFreeFarm)

Willkommen im MyFreeFarm-Projekt. Diese Datei dient KI-Agenten als primäre Orientierung und Regelsatz für die Arbeit an diesem Repository.

---

## 1. Projektübersicht & Hauptziel

- **Projekt:** MyFreeFarm Companion & Automation Backend.
- **Ist-Zustand:** Node.js / Express.js Backend mit asynchronen Workern (`co`, `unirest`, `kew`), die mit den Browsergame-Servern (`myfreefarm.de`) interagieren und eine REST-API (`/api/v1/*`) für Dashboards bereitstellen.
- **Hauptziel:** **Vollständiges Redesign der Applikation in modernem Python** (FastAPI, Pydantic v2, HTTPX, Asyncio).

---

## 2. Zentrales Wissensmanagement: LLM-Wiki (`llm-wiki/`)

Bevor du Code analysierst, refaktorisierst oder neu implementierst, **musst du das LLM-Wiki im Verzeichnis [`llm-wiki/`](./llm-wiki/) konsultieren**.
Das Wiki ist im **Open Knowledge Format (OKF)** von Google strukturiert (YAML-Frontmatter + Markdown) und enthält die vollständige Dokumentation aller Module, Schnittstellen und Migrationspläne.

### Wichtigste Einstiegspunkte:
- **[`llm-wiki/00-README.md`](./llm-wiki/00-README.md):** Gesamtübersicht, Inhaltsverzeichnis und Workflow zum Hinzufügen neuer Dokumente.
- **[`llm-wiki/12-python-redesign-spec.md`](./llm-wiki/12-python-redesign-spec.md):** Der verbindliche Ziel-Architektur-Blueprint und 5-Phasen-Migrationsplan nach Python.
- **[`llm-wiki/11-external-game-api.md`](./llm-wiki/11-external-game-api.md):** Externe Spielserver-Schnittstelle (Login, RID-Handling, AJAX-Endpunkte).
- **[`llm-wiki/10-express-routes-api.md`](./llm-wiki/10-express-routes-api.md):** Interne REST-API Spezifikation (Endpunkte, DTOs, JWT-Auth).
- **[`llm-wiki/09-core-services-stock.md`](./llm-wiki/09-core-services-stock.md):** Zentrale Lager- und Warenwirtschaft (`Stock`, `Market`, `Trade`, `Contracts`).
- **Modul-Dokumentationen (`03` bis `08`):**
  - `03-module-agriculture.md` (Ackerbau, Felder, Sushibar, Fahrzeug)
  - `04-module-forestry.md` (Baumerei, Sägewerk, Schreinerei, Forstwirt)
  - `05-module-farm.md` (Tierställe, Futter-Optimierung, Fabriken)
  - `06-module-farmersmarket.md` (Gärtnerei, Blumenzucht, Tierzucht)
  - `07-module-stall.md` (Obststand / Marktbude)
  - `08-module-misc.md` (Insektenhotel, Foodworld, Mühle, Tägliche Boni)

---

## 3. Entwicklungsrichtlinien für das Python-Redesign

1. **Python-Standards:**
   - Mindestens Python 3.12.
   - Konsequente Typisierung (`typing` / Built-in Generics).
   - Async-first: Verwende `async`/`await` und `httpx.AsyncClient` für HTTP-Calls (keine synchronen Requests-Bibliotheken im Worker).
2. **Datenvalidierung & Models:**
   - Verwende Pydantic v2 für alle externen Datenstrukturen (Upstream Game Responses und API Request/Response Schemata).
3. **Dokumentationspflicht:**
   - Werden neue Module portiert oder Architekturänderungen vorgenommen, ist das entsprechende Dokument in `llm-wiki/` zu aktualisieren bzw. neue Dokumente im OKF-Standard anzulegen (siehe [`llm-wiki/00-README.md`](./llm-wiki/00-README.md)).
