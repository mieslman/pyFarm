# MyFreeFarm Python Redesign

Modernes Python 3.11+ Backend und Automations-Engine für MyFreeFarm (Single-Account, 10-Minuten-Polling, reine dateibasierte Konfiguration).

## Architektur & Dokumentation
Die vollständige Spezifikation und Moduldokumentation befindet sich im zentralen LLM-Wiki unter:
- [`../llm-wiki/00-README.md`](../llm-wiki/00-README.md) (Übersicht & Inhaltsverzeichnis)
- [`../llm-wiki/12-python-redesign-spec.md`](../llm-wiki/12-python-redesign-spec.md) (Gesamtarchitektur & Blueprint)
- [`../llm-wiki/13-python-models-and-algorithms.md`](../llm-wiki/13-python-models-and-algorithms.md) (Pydantic-Modelle & Algorithmen)
- [`../llm-wiki/14-python-worker-and-scheduler.md`](../llm-wiki/14-python-worker-and-scheduler.md) (Worker-Lifecycle, 10-Min-Loop & Anti-Ban)

---

## Setup & Toolchain mit `uv`

Wir empfehlen [**`uv`** von Astral](https://github.com/astral-sh/uv) als extrem schnellen Paket- und Virtualenv-Manager (oder alternativ das mitgelieferte `.venv`).

### 1. `uv` installieren (falls noch nicht vorhanden)
```powershell
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Projekt synchronisieren
```powershell
# Virtuelle Umgebung automatisch anlegen und Abhängigkeiten installieren
uv venv
uv pip install -e ".[dev]"
```

### 3. Konfiguration (.env) einrichten
```powershell
# Vorlage kopieren
Copy-Item .env.example .env

# Zugangsdaten in .env eintragen (Server, Username, Password)
```

---

## Ausführung & Tests

### Offline-Tests ausführen (Standard)
Führt alle schnellen, deterministischen Mock-Tests mit `respx` aus:
```powershell
uv run pytest
# Alternativ: .venv\Scripts\pytest
```

### Live-Tests ausführen (Gegen echten Spielserver)
Führt die End-to-End Tests gegen den Live-Server mit den Zugangsdaten aus der `.env` aus:
```powershell
uv run pytest -m live -s
# Alternativ: .venv\Scripts\pytest -m live -s
```
*(Hinweis: Falls keine Zugangsdaten in `.env` hinterlegt sind, wird der Live-Test automatisch übersprungen.)*

### Direkter CLI Smoke-Test
Testet den echten Login und das Abrufen der Farmübersicht direkt auf der Konsole (Passwörter und Token werden im Log automatisch maskiert):
```powershell
# Nutzt die Zugangsdaten aus der .env:
uv run python -m app.core.client

# Oder mit expliziten Parametern:
uv run python -m app.core.client --server 1 --username "MeinUser" --password "MeinPasswort"
```

### Entwicklungsserver (FastAPI) starten
```powershell
uv run uvicorn app.main:app --reload --port 8000
```
OpenAPI Dokumentation im Browser: `http://127.0.0.1:8000/docs`
