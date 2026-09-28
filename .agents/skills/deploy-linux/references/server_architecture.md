# Linux Server-Architektur & Deployment-Referenz

Diese Referenz fasst die Zielumgebung und Konfigurationsparameter für das pyFarm Linux-Deployment zusammen (basierend auf `llm-wiki/22-linux-deployment-guide.md`).

---

## 1. Server-Eckdaten

| Parameter | Wert | Beschreibung |
| :--- | :--- | :--- |
| **Hostname / Domain** | `mff.miessl.net` (IP: `85.214.54.58`) | Extern erreichbare Domain mit Let's Encrypt SSL |
| **Betriebssystem** | Ubuntu 22.04 LTS | Linux Server |
| **SSH-Benutzer** | `manfred` | Standardbenutzer für Anwendung und Deployment |
| **Projektpfad** | `/home/manfred/pyFarm` | Repository Root auf dem Server |
| **Python Toolchain** | Astral `uv` (Python 3.12) | Virtuelle Umgebung unter `/home/manfred/pyFarm/.venv/` |
| **Systemd Service** | `pyfarm.service` | Unit unter `/etc/systemd/system/pyfarm.service` |
| **Reverse Proxy** | Nginx | Konfiguration unter `/etc/nginx/conf.d/mff.conf` |
| **Lokaler Port** | `127.0.0.1:8000` | Uvicorn ASGI Server bindet lokal |
| **Hauptlogdatei** | `/home/manfred/pyFarm/logs/myfreefarm.log` | Loguru Log (Rotation alle 10MB) |
| **Konfigurationsdatei** | `/home/manfred/pyFarm/data/user_config.json` | Farm- und Automations-Einstellungen |
| **Umgebungsdatei** | `/home/manfred/pyFarm/.env` | Zugangsdaten und Secrets (Berechtigung 600) |

---

## 2. SSH-Schlüssel einrichten (Passwortloses Deployment)

Für eine nahtlose Ausführung aller Befehle empfiehlt sich ein SSH-Schlüsselpaar:

1. **Prüfen oder generieren** (auf dem Windows-Entwicklungsrechner in PowerShell):
   ```powershell
   ssh-keygen -t ed25519 -C "antigravity-deploy"
   ```
2. **Öffentlichen Schlüssel auf den Server kopieren**:
   ```powershell
   Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" | ssh manfred@mff.miessl.net "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
   ```
3. **Testen**:
   ```powershell
   ssh -o BatchMode=yes manfred@mff.miessl.net echo "SSH Verbindung erfolgreich"
   ```

---

## 3. Sudoers-Berechtigungen (Passwortloses `systemctl`)

Damit der Dienst ohne manuelle Passworteingabe gestartet, gestoppt und neu gestartet werden kann, kann auf dem Server eine Sudoers-Regel eingerichtet werden:

```bash
# Auf dem Server ausführen:
sudo visudo -f /etc/sudoers.d/pyfarm
```

Inhalt hinzufügen:
```text
manfred ALL=(ALL) NOPASSWD: /usr/bin/systemctl start pyfarm, /usr/bin/systemctl stop pyfarm, /usr/bin/systemctl restart pyfarm, /usr/bin/systemctl status pyfarm, /usr/bin/journalctl
```

Berechtigung prüfen:
```bash
sudo chmod 440 /etc/sudoers.d/pyfarm
```

---

## 4. Systemd Service Unit (`/etc/systemd/system/pyfarm.service`)

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
