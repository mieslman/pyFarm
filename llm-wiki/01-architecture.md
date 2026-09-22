---
title: Aktuelle Architektur (JavaScript)
author: System
date: 2026-09-11
type: architecture
description: Übersicht über die aktuelle Systemarchitektur des MyFreeFarm Backends.
tags: [architecture, nodejs, express, pouchdb]
---
# Aktuelle Architektur

Die bestehende Anwendung ist ein monolithisches Backend, das in JavaScript (Node.js) geschrieben wurde.

## Technologie-Stack
- **Laufzeitumgebung:** Node.js
- **Web-Framework:** Express.js (v4.17.1)
- **Datenbank:** PouchDB (v7.2.2) für die Datenspeicherung (vermutlich lokal, dokumentenbasiert).
- **Logging:** Winston (v3.2.1)
- **HTTP-Client:** Unirest (v0.6.0) für externe API-Aufrufe.
- **Authentifizierung:** JSON Web Tokens (`jsonwebtoken`).

## Verzeichnisstruktur und Module
Das Projekt ist stark modularisiert aufgebaut, aufgeteilt nach den Domänen / Bereichen des Spiels:
- `agriculture/` - Ackerbau
- `forestry/` - Forstwirtschaft
- `foodworld/` - Lebensmittel/Verarbeitung
- `farmersmarket/` - Marktplatz
- `insecthotel/` - Insektenhotel
- `farm/`, `stall/`, `stock/` - Farm, Tiere und Lagerbestand

Die technische Struktur umfasst:
- `routes/` - Express Routing und API Endpunkte.
- `services/` - Kapselt die Business-Logik.
- `events/` - Steuerung asynchroner Events im Spielablauf.
- `helpers/` / `utils/` - Hilfsfunktionen.
- `data/` / `log/` - Laufzeitdaten und Logs.

## Zielarchitektur: Python Redesign
Das komplette Redesign der Applikation in Python sollte diese bestehende Domänen-Struktur übernehmen.
Empfohlene Technologien für das Python-Redesign:
- **Framework:** FastAPI oder Django.
- **Datenbank:** SQLAlchemy mit PostgreSQL/SQLite oder Beibehaltung einer NoSQL-Lösung wie MongoDB, falls die PouchDB-Dokumentenstruktur tief verwurzelt ist.
- **Strukturierung:** Jedes JavaScript-Modul (z.B. `agriculture`) wird zu einem eigenen Python-Package oder einer App.
