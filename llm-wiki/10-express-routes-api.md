---
title: Express REST-API Schnittstellen (Backend zu Dashboard)
author: System
date: 2026-09-11
type: code
description: Vollständige Dokumentation der internen Express REST API Endpunkte, Request/Response Schemata und JWT-Authentifizierung.
tags: [api, express, rest, jwt, routes, interfaces]
---
# Express REST-API (Backend zu Dashboard)

Die Node.js-Anwendung stellt auf Port `3100` eine REST-Schnittstelle unter dem Präfix `/api/v1/` bereit. Sie dient einem Web-Frontend / Dashboard zur Überwachung und Konfiguration der Automatisierung.

## 1. Globale Middleware & Authentifizierung

- **CORS:** Erlaubt alle Origins (`*`), Methoden `GET, PUT, POST, DELETE, OPTIONS`.
- **Parser:** `express.json()`
- **Authentifizierung:** `Login.authenticateJWT`
  - Header: `Authorization: Bearer <JWT-Token>`
  - Signatur: `jwt.verify(token, 'youraccesstokensecret')`
  - Schlägt fehl mit HTTP 401 (kein Header) oder HTTP 403 (ungültiges Token).

---

## 2. API Endpunkte

### 2.1 Login Router (`routes/login.js`)
- **Pfad:** `POST /api/v1/login`
- **Auth:** Öffentlich
- **Request Body:**
  ```json
  {
    "username": "mff",
    "password": "..."
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
  }
  ```
- **Gültigkeit:** 3600 Sekunden (1 Stunde).

---

### 2.2 Pflanzen / Produkte (`routes/plants.js`)
- **Pfad:** `GET /api/v1/plants`
  - Optional Query: `?category=v` (filtert z.B. nur Pflanzen)
- **Pfad:** `GET /api/v1/plants/:id`
  - Filtert nach bestimmter `pid`
- **Response:** Array von Produkt-Objekten (`pid`, `name`, `price`, `x`, `y`, `category`, `amount`, `tmpAmount`).

---

### 2.3 Lager & Bestellungen (`routes/stock.js`)
- **Pfad:** `GET /api/v1/stock/orders`
  - Gibt konfigurierte Nachbestellungen zurück (angereichert mit dem aktuellen `plant`-Objekt).
- **Pfad:** `PUT /api/v1/stock/orders`
  - Speichert neue Bestell-Konfiguration im `Config`-Store.

---

### 2.4 Farmen (`routes/farms.js`)
- **Pfad:** `GET /api/v1/farms`
  - Liefert Konfiguration der Ackerbau-Farmen (`agriculture.farms`), inklusive Pflanzen-Objekten und dem Attribut `category` (`v`, `ex`, `alpin`, `water`, `spice`).
- **Pfad:** `PUT /api/v1/farms`
  - Aktualisiert die Konfiguration der Ackerbau-Farmen (im Python-Backend mit strikter Prüfung gegen die Farm-Kategorie).

---

### 2.5 Markt-Verkaufsangebote (`routes/offers.js`)
- **Pfad:** `GET /api/v1/offers`
  - Liefert die konfigurierten automatischen Marktverkäufe (`trade.sell`).
- **Pfad:** `PUT /api/v1/offers`
  - Aktualisiert automatische Marktverkäufe.

---

### 2.6 Spieler-Verträge (`routes/contracts.js`)
- **Pfad:** `GET /api/v1/contracts`
  - Lädt konfigurierte Verträge aus `Contracts.store`.
  - Fragt live aktuelle Marktpreise vom Spieleserver ab (`mode: 'marketinit'`).
  - Berechnet `inStock`, `marketPrice`, `stockPrice`, `ready` (ob Vertrag erfüllbar ist).
- **Pfad:** `PUT /api/v1/contracts`
  - Aktualisiert Vertragseinstellungen.

---

### 2.7 Foodworld, Bauernmarkt & Forstwirtschaft
- **`GET /api/v1/forestry/stock`**: Liefert aktuelle Holz- und Möbelbestände.
- **`GET /api/v1/forestry/orders`** / **`PUT /api/v1/forestry/orders`**: Verwaltet Sägewerks-/Schreinereiaufträge.
- **`GET /api/v1/farmersmarket`** / **`GET/PUT /api/v1/farmersmarket/settings`**: Vollständiger Status und Konfiguration von Gärtnerei, Blumenbeeten und Zucht.
- **`GET /api/v1/foodworld`** / **`GET/PUT /api/v1/foodworld/settings`**: Vollständiger Live-Status (4 Küchen, Tische, Gäste, Lager) und Konfiguration (Puffer 50, Tischkauf-Schutz, Marktexport bei Leerstand).
- **`GET /api/v1/foodworld/orders`** / **`PUT /api/v1/foodworld/orders`**: Legacy-Bestellverwaltung für Speisen.

---

## 3. Python-Redesign Empfehlung (FastAPI)

In Python lässt sich diese API 1:1 mit **FastAPI** und **Pydantic** nachbilden:

```python
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

app = FastAPI(title="MyFreeFarm Companion API", version="2.0.0")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

@app.post("/api/v1/login", response_model=TokenResponse)
async def login(credentials: LoginRequest):
    ...

@app.get("/api/v1/plants")
async def get_plants(category: Optional[str] = None, token: str = Depends(oauth2_scheme)):
    ...
```
- Automatische OpenAPI / Swagger Dokumentation unter `/docs`.
- Strikte Validierung aller Eingabedaten über Pydantic Schemas.
