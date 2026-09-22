---
title: Linux Server Deployment Guide (Ubuntu 22.04 LTS, Nginx, Systemd, SSL)
author: System
date: 2026-09-22
type: architecture
description: Schritt-für-Schritt Deployment-Leitfaden für pyFarm auf Ubuntu 22.04 LTS mit uv (Python 3.12), Systemd Daemon, Nginx Reverse Proxy mit WebSocket-Unterstützung und Let's Encrypt SSL unter mff.miessl.net (Benutzer manfred, Pfad /home/manfred/pyFarm).
tags: [deployment, ubuntu, nginx, systemd, ssl, uv, python, production]
---

# Linux Server Deployment Guide (Ubuntu 22.04 LTS)

Dieses Dokument beschreibt das vollständige, produktionsreife Deployment von **pyFarm** auf einem Linux-Server mit **Ubuntu 22.04 LTS** für den Benutzer **`manfred`** im Pfad **`/home/manfred/pyFarm`**.

Es verwendet den modernen Paket- und Python-Manager **`uv`**, **Systemd** als Prozess-Supervisor, **Nginx** als Reverse Proxy (inklusive WebSocket-Unterstützung für das Live-Dashboard) und automatisches **Let's Encrypt SSL-Zertifikat**.

---

## 1. Architektur-Übersicht

```
[ Browser / Client ]
         │  HTTPS / WSS (Port 443)
         ▼
[ Nginx Reverse Proxy (mff.miessl.net) ]
         │  HTTP / WS (Port 8000 auf 127.0.0.1)
         ▼
[ Uvicorn ASGI Server ]
         │
[ pyFarm Engine (/home/manfred/pyFarm) ]
         │
         ├── .env (Zugangsdaten & Secrets)
         └── data/user_config.json (Farm-Automations-Konfiguration)
```

---

## 2. Voraussetzungen & Systempakete

Ubuntu 22.04 liefert standardmäßig Python 3.10 aus. Da `pyFarm` mindestens **Python 3.11+** voraussetzt, nutzen wir **`uv`** von Astral. `uv` lädt bei Bedarf eigenständig eine isolierte, optimierte Python 3.12-Laufzeitumgebung herunter, ohne Systembibliotheken zu berühren.

### 2.1 Pakete installieren
Als Benutzer `manfred` per SSH auf dem Server:
```bash
sudo apt update && sudo apt install -y git curl nginx certbot python3-certbot-nginx
```

### 2.2 `uv` installieren
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.cargo/env
```

---

## 3. Quellcode klonen

Das Projekt wird direkt im Home-Verzeichnis unter `/home/manfred/pyFarm` geklont:

```bash
cd /home/manfred
git clone https://github.com/mieslman/pyFarm.git /home/manfred/pyFarm
cd /home/manfred/pyFarm
```

---

## 4. Python 3.12 Umgebung initialisieren (`uv`)

Im Projektverzeichnis `/home/manfred/pyFarm`:

```bash
cd /home/manfred/pyFarm

# Python 3.12 Laufzeitumgebung herunterladen und venv mit allen Dependencies synchronisieren
uv python install 3.12
uv sync
```

Dadurch wird das virtuelle Environment `/home/manfred/pyFarm/.venv/` mit FastAPI, Uvicorn, Pydantic, HTTPX und allen weiteren Paketen erstellt.

---

## 5. Konfiguration (`.env`)

Erstellen Sie die Konfigurationsdatei `/home/manfred/pyFarm/.env`:

```bash
nano /home/manfred/pyFarm/.env
```

Inhalt:
```ini
# MyFreeFarm Spiel-Zugangsdaten
MFF_ACCOUNT__SERVER=21
MFF_ACCOUNT__USERNAME=Piginator
MFF_ACCOUNT__PASSWORD=IhrGeheimesSpielPasswort

# Web-Dashboard Login
MFF_API_USERNAME=mff
MFF_API_PASSWORD=IhrSicheresDashboardPasswort

# Sicherheit (bitte einen zufälligen String mit min. 32 Zeichen erzeugen)
MFF_SECRET_KEY=change-this-in-production-super-secret-key-32chars

# Server-Einstellungen
MFF_HOST=127.0.0.1
MFF_PORT=8000
MFF_DEBUG=False
```

Berechtigungen auf `.env` einschränken (nur `manfred` darf lesen):
```bash
chmod 600 /home/manfred/pyFarm/.env
```

---

## 6. Systemd-Dienst einrichten (`pyfarm.service`)

Erstellen Sie die Service-Unit-Datei für Systemd:

```bash
sudo nano /etc/systemd/system/pyfarm.service
```

Konfiguration:
```ini
[Unit]
Description=pyFarm Automation Backend
After=network.target

[Service]
Type=simple
User=manfred
WorkingDirectory=/home/manfred/pyFarm
ExecStart=/home/manfred/pyFarm/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10
EnvironmentFile=/home/manfred/pyFarm/.env

[Install]
WantedBy=multi-user.target
```

Aktivieren und Starten:
```bash
sudo systemctl daemon-reload
sudo systemctl enable pyfarm
sudo systemctl start pyfarm
```

Status überprüfen:
```bash
sudo systemctl status pyfarm
```

---

## 7. Nginx Reverse Proxy & SSL (`/etc/nginx/conf.d/mff.conf`)

Die Nginx-Konfiguration wird direkt unter `/etc/nginx/conf.d/mff.conf` gepflegt. Eine zusätzliche Datei in `sites-available` bzw. `sites-enabled` ist **nicht erforderlich** (und sollte entfernt werden, um Konflikte durch doppelte `server_name`-Direktiven zu vermeiden).

Da das pyFarm-Dashboard Echtzeit-Zustands-Updates über WebSockets (`/api/v1/ws`) bezieht, müssen die Upgrade-Header korrekt konfiguriert werden.

### 7.1 Konfigurationsdatei anpassen

```bash
sudo nano /etc/nginx/conf.d/mff.conf
```

Inhalt (inklusive bestehender Let's Encrypt / Certbot SSL-Zertifikate):
```nginx
server {
    server_name mff.miessl.net;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        # WebSocket Upgrade-Header (essentiell für das Live-Dashboard /api/v1/ws)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Standard Proxy-Header
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts für langlebige WebSocket-Verbindungen
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }

    # Certbot SSL-Konfiguration
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/mff.miessl.net/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/mff.miessl.net/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot
}

server {
    if ($host = mff.miessl.net) {
        return 301 https://$host$request_uri;
    } # managed by Certbot

    listen      80;
    server_name mff.miessl.net;
    return 404; # managed by Certbot
}
```

### 7.2 Bereinigung & Nginx neu laden

Falls zuvor Dateien in `sites-available` / `sites-enabled` angelegt wurden, diese entfernen:
```bash
sudo rm -f /etc/nginx/sites-enabled/mff.miessl.net
sudo rm -f /etc/nginx/sites-available/mff.miessl.net
```

Konfiguration testen und Nginx neu laden:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## 8. Wartung & Betriebsführung

### Live-Logs einsehen
```bash
# Systemd-Dienst-Logs in Echtzeit verfolgen
sudo journalctl -u pyfarm -f -n 100
```

### Neustart & Stopp
```bash
sudo systemctl restart pyfarm
sudo systemctl stop pyfarm
```

### Updates von GitHub einspielen
```bash
cd /home/manfred/pyFarm
git pull
uv sync
sudo systemctl restart pyfarm
```

### Konfigurations-Backup
Die automatisierten Einstellungen der Farmen werden kontinuierlich atomar in `/home/manfred/pyFarm/data/user_config.json` persistiert. Diese Datei kann bei Bedarf gesichert werden:
```bash
cp /home/manfred/pyFarm/data/user_config.json /home/manfred/pyFarm/data/user_config.json.bak
```
