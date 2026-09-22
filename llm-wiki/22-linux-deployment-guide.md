---
title: Linux Server Deployment Guide (Ubuntu 22.04 LTS, Nginx, Systemd, SSL)
author: System
date: 2026-09-22
type: architecture
description: Schritt-für-Schritt Deployment-Leitfaden für pyFarm auf Ubuntu 22.04 LTS mit uv (Python 3.12), Systemd Daemon, Nginx Reverse Proxy mit WebSocket-Unterstützung und Let's Encrypt SSL unter mff.miessl.net.
tags: [deployment, ubuntu, nginx, systemd, ssl, uv, python, production]
---

# Linux Server Deployment Guide (Ubuntu 22.04 LTS)

Dieses Dokument beschreibt das vollständige, produktionsreife Deployment von **pyFarm** auf einem Linux-Server mit **Ubuntu 22.04 LTS** unter Verwendung des modernen Paket- und Python-Managers **`uv`**, **Systemd** als Prozess-Supervisor, **Nginx** als Reverse Proxy (inklusive WebSocket-Unterstützung für das Live-Dashboard) und automatischem **Let's Encrypt SSL-Zertifikat**.

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
[ pyFarm Engine (FastAPI + WorkerScheduler) ]
         │
         ├── .env (Zugangsdaten & Secrets)
         └── data/user_config.json (Farm-Automations-Konfiguration)
```

---

## 2. Voraussetzungen & Systempakete

Ubuntu 22.04 liefert standardmäßig Python 3.10 aus. Da `pyFarm` mindestens **Python 3.11+** voraussetzt, nutzen wir **`uv`** von Astral. `uv` lädt bei Bedarf eigenständig eine isolierte, optimierte Python 3.12-Laufzeitumgebung herunter, ohne Systembibliotheken zu berühren.

### 2.1 Pakete installieren
```bash
sudo apt update && sudo apt install -y git curl nginx certbot python3-certbot-nginx
```

### 2.2 `uv` installieren
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.cargo/env
```

---

## 3. Quellcode klonen & Berechtigungen

Empfohlenes Installationsverzeichnis ist `/opt/pyfarm`:

```bash
# Verzeichnis anlegen und Benutzerberechtigung zuweisen
sudo mkdir -p /opt/pyfarm
sudo chown -R $USER:$USER /opt/pyfarm

# Von GitHub klonen
git clone https://github.com/mieslman/pyFarm.git /opt/pyfarm
cd /opt/pyfarm
```

---

## 4. Python-Umgebung initialisieren (`uv`)

Im Projektverzeichnis `/opt/pyfarm`:

```bash
cd /opt/pyfarm

# Python 3.12 Laufzeitumgebung herunterladen und venv mit allen Dependencies synchronisieren
uv python install 3.12
uv sync
```

Dadurch wird das virtuelle Environment `/opt/pyfarm/.venv/` mit FastAPI, Uvicorn, Pydantic, HTTPX und allen weiteren Paketen erstellt.

---

## 5. Konfiguration (`.env`)

Erstellen Sie die Konfigurationsdatei `/opt/pyfarm/.env`:

```bash
nano /opt/pyfarm/.env
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

Berechtigungen auf `.env` einschränken:
```bash
chmod 600 /opt/pyfarm/.env
```

---

## 6. Systemd-Dienst einrichten (`pyfarm.service`)

Erstellen Sie die Service-Unit-Datei für Systemd:

```bash
sudo nano /etc/systemd/system/pyfarm.service
```

Konfiguration (ersetzen Sie `DEIN_BENUTZERNAME` durch Ihren Linux-Benutzer, z. B. `ubuntu`):
```ini
[Unit]
Description=pyFarm Automation Backend
After=network.target

[Service]
Type=simple
User=DEIN_BENUTZERNAME
WorkingDirectory=/opt/pyfarm
ExecStart=/opt/pyfarm/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10
EnvironmentFile=/opt/pyfarm/.env

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

## 7. Nginx Reverse Proxy (mit WebSocket-Support)

Da das pyFarm-Dashboard Echtzeit-Zustands-Updates über WebSockets (`/api/v1/ws`) bezieht, müssen die Upgrade-Header korrekt konfiguriert werden.

Erstellen Sie `/etc/nginx/sites-available/mff.miessl.net`:

```bash
sudo nano /etc/nginx/sites-available/mff.miessl.net
```

Inhalt:
```nginx
server {
    listen 80;
    server_name mff.miessl.net;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        # WebSocket Upgrade-Header (essentiell für /api/v1/ws Live-Dashboard)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Standard Proxy-Header
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
```

Site verlinken, Syntax testen und Nginx neu laden:
```bash
sudo ln -s /etc/nginx/sites-available/mff.miessl.net /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 8. SSL-Zertifikat via Let's Encrypt (Certbot)

Sobald der DNS A-Record für `mff.miessl.net` auf die IP-Adresse des Servers auflöst:

```bash
sudo certbot --nginx -d mff.miessl.net
```

Certbot erweitert die Nginx-Konfiguration automatisch um HTTPS (Port 443), richtet Weiterleitungen von Port 80 ein und konfiguriert einen automatischen Cron/Timer für Zertifikats-Erneuerungen.

---

## 9. Wartung & Betriebsführung

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
cd /opt/pyfarm
git pull
uv sync
sudo systemctl restart pyfarm
```

### Konfigurations-Backup
Die automatisierten Einstellungen der Farmen werden kontinuierlich atomar in `/opt/pyfarm/data/user_config.json` persistiert. Diese Datei kann bei Bedarf gesichert werden:
```bash
cp /opt/pyfarm/data/user_config.json /opt/pyfarm/data/user_config.json.bak
```
