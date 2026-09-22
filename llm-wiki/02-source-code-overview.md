---
title: Source Code Übersicht
author: System
date: 2026-09-11
type: code
description: Dokumentation des bestehenden Source Codes und Einstiegspunkte.
tags: [code, index.js, mffserver.js, python-migration]
---
# Source Code Übersicht

Dieses Dokument bietet einen Überblick über den Einstieg in den bestehenden Source Code und was bei der Python-Migration zu beachten ist.

## Einstiegspunkte
- `../index.js`: Der primäre Einstiegspunkt des Node.js Servers (siehe `main` in `package.json`). Hier wird typischerweise der Server gestartet und die grundlegende Konfiguration geladen.
- `../mffserver.js`: Beinhaltet sehr wahrscheinlich die Express-App-Konfiguration, Middleware-Initialisierung und das Einhängen der Routen.
- `../Modules.js`: Möglicherweise eine zentrale Registry oder ein Loader für die verschiedenen Spiel-Module.

## Konfiguration und Statische Daten
- `../config.json` / `../myfreefarm.json`: Zentrale Konfigurationsdateien.
- `../Pflanzen.txt` / `../Baumerei.json`: Enthalten statische Spieldaten (z.B. Pflanzeneigenschaften, Wachstumszeiten, Baumerei-Konfiguration).

## Redesign-Vorgehen für den Source Code
Beim Portieren auf Python (z.B. mit FastAPI) wird folgendes Vorgehen empfohlen:
1. **Datenmodelle isolieren:** Lese die statischen Dateien (`Pflanzen.txt`, `Baumerei.json`) aus und modelliere diese als Python `pydantic` Klassen oder `dataclasses`.
2. **Datenbankschicht (PouchDB):** Analysiere die PouchDB Aufrufe in den `services/` und entscheide, ob das neue Python-Backend ein lokales SQLite, ein JSON-basiertes System (wie TinyDB) oder eine vollwertige Datenbank (PostgreSQL) nutzt.
3. **Routen migrieren:** Portiere die Express-Routen aus `../routes/` schrittweise in FastAPI-Router.
4. **Logik portieren:** Übersetze die Business-Logik aus den domänenspezifischen Ordnern (`../agriculture/`, `../forestry/`, etc.) in Python-Services. Achte dabei auf die Umwandlung von JavaScript-Promises/Async-Logik (bspw. `co` / `co-flow` Bibliotheken) in Pythons modernes `asyncio`.
