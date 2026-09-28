---
name: deploy-linux
description: >-
  Führt das Deployment, die Betriebsführung und die Konfiguration der pyFarm-Applikation
  auf dem entfernten Linux-Server (Ubuntu, Systemd, Nginx) durch. Verwende diesen Skill,
  wenn der Benutzer nach dem Aktualisieren des Repositories auf dem Server, dem Starten/Stoppen/Neustarten
  des pyfarm-Dienstes, der Überprüfung des Service-Status, der lokalen Log-Analyse
  oder der Anpassung der Farm-Konfiguration (user_config.json) fragt.
---

# Linux Server Deployment & Management Runbook

Dieser Skill steuert das Deployment und die Administration von **pyFarm** auf dem Linux-Produktionsserver.

## Schnellübersicht & Werkzeuge

* **Zentrales Skript**: [deploy_manager.py](./scripts/deploy_manager.py)
* **Log-Analyse-Modul**: [analyze_logs.py](./scripts/analyze_logs.py)
* **Server-Referenz**: [server_architecture.md](./references/server_architecture.md)
* **Konfigurationsleitfaden**: [user_config_guide.md](./references/user_config_guide.md)

Standard-Serverdaten (über Argumente oder `.env` anpassbar):
* **Host**: `mff.miessl.net`
* **SSH-User**: `manfred`
* **Pfad**: `/home/manfred/pyFarm`
* **Service**: `pyfarm` (Systemd)

---

## 1. Verbindung & Authentifizierung

Der Skill unterstützt sowohl passwortbasierte Authentifizierung als auch SSH-Keys:

### Option A: Passwort in `.env` (Standard)
Tragen Sie in Ihrer lokalen `.env` das Passwort ein:
```ini
MFF_DEPLOY_PASSWORD="IhrServerPasswort"
```
Der `deploy_manager` nutzt `paramiko` und authentifiziert sich bei SSH- und SFTP-Operationen sowie `sudo`-Befehlen vollautomatisch.

### Option B: SSH-Schlüssel (Passwortlos)
```powershell
# 1. Schlüsselpaar erzeugen (falls noch keines existiert)
ssh-keygen -t ed25519 -C "antigravity-deploy"

# 2. Public Key auf den Linux-Server übertragen
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" | ssh manfred@mff.miessl.net "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Verbindung prüfen:
```powershell
python .agents/skills/deploy-linux/scripts/deploy_manager.py setup-ssh
```

---

## 2. Repository auf dem Server aktualisieren

Aktualisiert den Quellcode via `git pull` und synchronisiert die Python-Laufzeitumgebung via `uv sync`:

```powershell
# Standard: Repository aktualisieren (warnt vor ungespeicherten lokalen Änderungen)
python .agents/skills/deploy-linux/scripts/deploy_manager.py update

# Optional: Repository aktualisieren und pyfarm-Dienst direkt neu starten
python .agents/skills/deploy-linux/scripts/deploy_manager.py update --restart
```

---

## 3. Service-Status prüfen

Prüft den Zustand des Systemd-Dienstes (`active`/`inactive`), zeigt den `systemctl status`-Auszug und testet den HTTP-Endpunkt:

```powershell
python .agents/skills/deploy-linux/scripts/deploy_manager.py status
```

---

## 4. Service starten, stoppen und neu starten

```powershell
# Dienst starten
python .agents/skills/deploy-linux/scripts/deploy_manager.py start

# Dienst stoppen
python .agents/skills/deploy-linux/scripts/deploy_manager.py stop

# Dienst neu starten (z.B. nach Code- oder Konfigurationsänderungen)
python .agents/skills/deploy-linux/scripts/deploy_manager.py restart
```

---

## 5. Logfile lokal analysieren

Lädt die Server-Logs herunter und führt eine strukturierte Analyse durch (inklusive Zyklen-Auswertung, Erkennung von Tracebacks, Warnungen und Modul-Aktivitäten):

```powershell
# Letzte 150 Zeilen herunterladen und automatisch analysieren:
python .agents/skills/deploy-linux/scripts/deploy_manager.py logs -n 150 --download --analyze

# Nur Fehler und Probleme anzeigen:
python .agents/skills/deploy-linux/scripts/deploy_manager.py logs -n 200 --download --analyze --errors-only

# Detailansicht des letzten Scheduler-Zyklus:
python .agents/skills/deploy-linux/scripts/deploy_manager.py logs -n 300 --download --analyze --last-cycle

# Alternativ: Logs aus dem Systemd-Journal (journalctl) lesen:
python .agents/skills/deploy-linux/scripts/deploy_manager.py logs --journal -n 100
```

Eine beliebige bereits lokal vorliegende Logdatei kann auch direkt ausgewertet werden:
```powershell
python .agents/skills/deploy-linux/scripts/analyze_logs.py logs/server_myfreefarm.log --last-cycle
```

---

## 6. Farm-Konfiguration (`user_config.json`) anpassen

Die Konfiguration steuert alle Automationsabläufe. Der Skill unterstützt Diffing, Pull, Push mit automatischem Server-Backup und gezieltes Setzen einzelner Werte:

### 6.1 Unterschiede anzeigen (`config-diff`)
Vergleicht die lokale `data/user_config.json` mit der Live-Version auf dem Server:
```powershell
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-diff
```

### 6.2 Konfiguration vom Server laden (`config-pull`)
Lädt die aktuelle Version vom Server herunter (erstellt zuvor ein lokales Backup `user_config.json.local.bak`):
```powershell
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-pull
```

### 6.3 Lokale Konfiguration zum Server hochladen (`config-push`)
Validiert die JSON-Syntax, erstellt auf dem Server ein Backup mit Zeitstempel (`user_config.json.bak.YYYYMMDD_HHMMSS`) und lädt die neue Version hoch:
```powershell
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-push

# Optional: Mit anschließendem automatischem Neustart des Dienstes:
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-push --restart
```

### 6.4 Gezielte Schlüsselwerte anpassen (`config-set` / `config-get`)
```powershell
# Einzelnen Wert abfragen
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-get agriculture.plant_strategy

# Einzelnen Wert ändern und direkt zum Server pushen:
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-set agriculture.auto_water true --push

# Wert ändern und Dienst direkt neu starten:
python .agents/skills/deploy-linux/scripts/deploy_manager.py config-set trade.enabled false --push --restart
```
