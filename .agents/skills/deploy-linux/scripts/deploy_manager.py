#!/usr/bin/env python3
"""
pyFarm Linux Deployment & Service Manager
Automates remote server operations:
- Updating repository via git pull & uv sync
- Managing systemd service (start, stop, restart, status)
- Fetching & analyzing logs
- Synchronizing and modifying data/user_config.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        # Silently ignore if stream reconfiguration is unsupported
        pass

# Optional Paramiko support for password-based SSH/SFTP
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    paramiko = None
    HAS_PARAMIKO = False

# Try importing the local log analyzer if available in the same folder
try:
    from analyze_logs import LogAnalyzer
except ImportError:
    try:
        from .analyze_logs import LogAnalyzer
    except Exception:
        LogAnalyzer = None


DEFAULT_HOST = "mff.miessl.net"
DEFAULT_USER = "manfred"
DEFAULT_PORT = 22
DEFAULT_REMOTE_PATH = "/home/manfred/pyFarm"
DEFAULT_SERVICE = "pyfarm"
DEFAULT_LOCAL_CONFIG = Path("data/user_config.json")
DEFAULT_REMOTE_CONFIG = "/home/manfred/pyFarm/data/user_config.json"
DEFAULT_REMOTE_LOG = "/home/manfred/pyFarm/logs/myfreefarm.log"


def load_env_defaults() -> dict[str, str]:
    """Reads defaults from .env if available."""
    config: dict[str, str] = {}
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "MFF_DEPLOY_HOST":
                config["host"] = v
            elif k == "MFF_DEPLOY_USER":
                config["user"] = v
            elif k == "MFF_DEPLOY_PORT":
                config["port"] = v
            elif k == "MFF_DEPLOY_PATH":
                config["path"] = v
            elif k == "MFF_DEPLOY_KEY":
                config["key"] = v
            elif k == "MFF_DEPLOY_SERVICE":
                config["service"] = v
            elif k in ("MFF_DEPLOY_PASSWORD", "DEPLOY_PASSWORD"):
                config["password"] = v
    return config


class RemoteClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        user: str = DEFAULT_USER,
        port: int = DEFAULT_PORT,
        remote_path: str = DEFAULT_REMOTE_PATH,
        service: str = DEFAULT_SERVICE,
        key_file: str | None = None,
        password: str | None = None,
        batch_mode: bool = True,
    ):
        self.host = host
        self.user = user
        self.port = port
        self.remote_path = remote_path
        self.service = service
        self.key_file = key_file
        self.password = password
        self.batch_mode = batch_mode
        self._paramiko_client: Any = None

    def _get_paramiko_client(self) -> Any:
        if not HAS_PARAMIKO:
            return None
        if self._paramiko_client is not None:
            return self._paramiko_client
        try:
            pclient = paramiko.SSHClient()
            pclient.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            pclient.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                key_filename=self.key_file,
                timeout=15,
                look_for_keys=True,
                allow_agent=True,
            )
            self._paramiko_client = pclient
            return pclient
        except Exception:
            return None

    def _build_ssh_cmd(self, remote_command: str, tty: bool = False, batch_mode: bool = True) -> list[str]:
        cmd = ["ssh"]
        if tty:
            cmd.append("-t")
        if self.key_file:
            cmd.extend(["-i", self.key_file])
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        cmd.extend([
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
        ])
        if batch_mode:
            cmd.extend(["-o", "BatchMode=yes"])
        cmd.extend([
            f"{self.user}@{self.host}",
            remote_command,
        ])
        return cmd

    def run_ssh(
        self,
        remote_command: str,
        capture: bool = True,
        check: bool = False,
        timeout: int = 60,
        batch_mode: bool | None = None,
    ) -> subprocess.CompletedProcess[str]:
        # Use Paramiko if password is provided or key file is set and paramiko is available
        if HAS_PARAMIKO and (self.password or self.key_file):
            pclient = self._get_paramiko_client()
            if pclient is not None:
                actual_cmd = remote_command
                need_sudo_input = False
                if "sudo " in remote_command and self.password:
                    # Use -S to read password from stdin and -p '' to avoid prompt noise
                    actual_cmd = remote_command.replace("sudo ", "sudo -S -p '' ")
                    need_sudo_input = True
                try:
                    stdin, stdout, stderr = pclient.exec_command(actual_cmd, timeout=timeout)
                    if need_sudo_input and self.password:
                        stdin.write(self.password + "\n")
                        stdin.flush()
                    stdout_str = stdout.read().decode("utf-8", errors="replace")
                    stderr_str = stderr.read().decode("utf-8", errors="replace")
                    exit_code = stdout.channel.recv_exit_status()
                    return subprocess.CompletedProcess(
                        args=[remote_command],
                        returncode=exit_code,
                        stdout=stdout_str,
                        stderr=stderr_str,
                    )
                except Exception as e:
                    return subprocess.CompletedProcess(
                        args=[remote_command],
                        returncode=1,
                        stdout="",
                        stderr=f"Paramiko SSH Fehler: {e}",
                    )

        # Fallback to OpenSSH subprocess
        effective_batch = self.batch_mode if batch_mode is None else batch_mode
        cmd = self._build_ssh_cmd(remote_command, batch_mode=effective_batch)
        try:
            return subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                check=check,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            print(f"[FEHLER] SSH-Befehl lief in einen Timeout ({timeout}s): {remote_command}", file=sys.stderr)
            return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="TimeoutExpired")
        except FileNotFoundError:
            print("[FEHLER] 'ssh'-Befehl wurde im System-PATH nicht gefunden.", file=sys.stderr)
            sys.exit(1)

    def run_scp(
        self,
        source: str,
        target: str,
        is_upload: bool = True,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        # Use Paramiko SFTP if password or key file is set
        if HAS_PARAMIKO and (self.password or self.key_file):
            pclient = self._get_paramiko_client()
            if pclient is not None:
                try:
                    sftp = pclient.open_sftp()
                    if is_upload:
                        sftp.put(source, target)
                    else:
                        sftp.get(source, target)
                    sftp.close()
                    return subprocess.CompletedProcess(
                        args=["scp", source, target],
                        returncode=0,
                        stdout="",
                        stderr="",
                    )
                except Exception as e:
                    return subprocess.CompletedProcess(
                        args=["scp", source, target],
                        returncode=1,
                        stdout="",
                        stderr=f"SFTP Fehler: {e}",
                    )

        # Fallback to OpenSSH scp subprocess
        cmd = ["scp"]
        if self.key_file:
            cmd.extend(["-i", self.key_file])
        if self.port != 22:
            cmd.extend(["-P", str(self.port)])
        cmd.extend([
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
        ])

        if is_upload:
            cmd.extend([source, f"{self.user}@{self.host}:{target}"])
        else:
            cmd.extend([f"{self.user}@{self.host}:{source}", target])

        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            print(f"[FEHLER] SCP-Transfer lief in einen Timeout ({timeout}s).", file=sys.stderr)
            return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="TimeoutExpired")

    def test_connection(self) -> bool:
        res = self.run_ssh("echo __OK__", capture=True, timeout=10, batch_mode=True)
        if res.returncode == 0 and "__OK__" in res.stdout:
            return True
        print("[HINWEIS] SSH-Verbindung fehlgeschlagen:", file=sys.stderr)
        if "Permission denied" in res.stderr:
            print(" -> Authentifizierung abgewiesen. Prüfen Sie Passwort oder SSH-Key.", file=sys.stderr)
        elif res.stderr:
            print(f" -> {res.stderr.strip()}", file=sys.stderr)
        return False


def cmd_status(client: RemoteClient, args: argparse.Namespace) -> int:
    """Checks the status of the remote service."""
    print(f"[*] Prüfe Dienst '{client.service}' auf {client.user}@{client.host}...")

    # Check is-active
    res_active = client.run_ssh(f"systemctl is-active {client.service}")
    active_state = res_active.stdout.strip() if res_active.stdout.strip() else ("active" if res_active.returncode == 0 else "unknown")

    print(f"Status: {active_state.upper()}")

    # Detailed status output
    res_details = client.run_ssh(f"systemctl status {client.service} --no-pager")
    if res_details.stdout:
        print("\n--- systemctl status ---")
        print(res_details.stdout.strip())
    elif res_details.stderr:
        print("\n--- systemctl Fehler ---")
        print(res_details.stderr.strip())

    # Check HTTP health
    print(f"\n[*] Prüfe HTTP-Endpunkt (https://{client.host})...")
    res_http = client.run_ssh(f"curl -s -o /dev/null -w '%{{http_code}}' https://{client.host}/ || curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:8000/")
    http_code = res_http.stdout.strip()
    if http_code:
        print(f"HTTP Antwortcode: {http_code}")
    return 0 if "active" in active_state else 1


def cmd_start(client: RemoteClient, args: argparse.Namespace) -> int:
    """Starts the remote service."""
    print(f"[*] Starte Dienst '{client.service}' auf {client.host}...")
    res = client.run_ssh(f"sudo systemctl start {client.service}")
    if res.returncode != 0:
        print(f"[FEHLER] Dienst konnte nicht gestartet werden:\n{res.stderr}", file=sys.stderr)
        return res.returncode
    print("[OK] Start-Befehl erfolgreich gesendet. Neuer Status:")
    return cmd_status(client, args)


def cmd_stop(client: RemoteClient, args: argparse.Namespace) -> int:
    """Stops the remote service."""
    print(f"[*] Stoppe Dienst '{client.service}' auf {client.host}...")
    res = client.run_ssh(f"sudo systemctl stop {client.service}")
    if res.returncode != 0:
        print(f"[FEHLER] Dienst konnte nicht gestoppt werden:\n{res.stderr}", file=sys.stderr)
        return res.returncode
    print("[OK] Dienst gestoppt.")
    res_active = client.run_ssh(f"systemctl is-active {client.service}")
    print(f"Aktueller Zustand: {res_active.stdout.strip()}")
    return 0


def cmd_restart(client: RemoteClient, args: argparse.Namespace) -> int:
    """Restarts the remote service."""
    print(f"[*] Starte Dienst '{client.service}' auf {client.host} neu...")
    res = client.run_ssh(f"sudo systemctl restart {client.service}")
    if res.returncode != 0:
        print(f"[FEHLER] Dienst konnte nicht neu gestartet werden:\n{res.stderr}", file=sys.stderr)
        return res.returncode
    print("[OK] Neustart-Befehl ausgeführt. Neuer Status:")
    return cmd_status(client, args)


def cmd_update(client: RemoteClient, args: argparse.Namespace) -> int:
    """Updates the repository on the server via git pull and uv sync."""
    if not args.skip_local_check:
        # Check local git status
        try:
            local_status = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=False,
            )
            if local_status.stdout.strip():
                print("[WARNUNG] Lokaler Git-Branch hat ungespeicherte / uncommitted Änderungen:")
                for l in local_status.stdout.splitlines()[:5]:
                    print(f"  {l}")
                print("Stelle sicher, dass wichtige Änderungen comitted und auf GitHub gepusht wurden.\n")
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    print(f"[*] Aktualisiere Repository auf Server ({client.remote_path})...")
    remote_script = (
        f"cd {client.remote_path} && "
        "git fetch origin && "
        "git pull && "
        "uv sync"
    )
    res = client.run_ssh(remote_script, timeout=120)
    print("--- Git & UV Output ---")
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr:
        print(res.stderr.strip())

    if res.returncode != 0:
        print(f"[FEHLER] Aktualisierung fehlgeschlagen (Exit Code {res.returncode})", file=sys.stderr)
        return res.returncode

    print("\n[OK] Repository und Abhängigkeiten erfolgreich aktualisiert.")

    if args.restart:
        print("\n[*] Führe automatischen Service-Neustart durch (--restart)...")
        return cmd_restart(client, args)

    return 0


def cmd_logs(client: RemoteClient, args: argparse.Namespace) -> int:
    """Fetches and/or analyzes remote logs."""
    lines_count = getattr(args, "lines", 100)
    use_journal = getattr(args, "journal", False)
    download_target = getattr(args, "download", None)
    run_analysis = getattr(args, "analyze", False)

    if use_journal:
        print(f"[*] Lese letzte {lines_count} Zeilen aus Systemd Journal ({client.service})...")
        remote_cmd = f"sudo journalctl -u {client.service} -n {lines_count} --no-pager"
    else:
        print(f"[*] Lese letzte {lines_count} Zeilen aus {DEFAULT_REMOTE_LOG}...")
        remote_cmd = f"tail -n {lines_count} {DEFAULT_REMOTE_LOG}"

    res = client.run_ssh(remote_cmd, timeout=60)
    if res.returncode != 0:
        print(f"[FEHLER] Konnte Logs nicht abrufen:\n{res.stderr}", file=sys.stderr)
        return res.returncode

    log_text = res.stdout

    if download_target or run_analysis:
        save_path = Path(download_target) if download_target else Path("logs/server_myfreefarm.log")
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(log_text, encoding="utf-8")
        print(f"[OK] Logfile lokal gespeichert unter: {save_path}")

        if run_analysis:
            if LogAnalyzer is not None:
                analyzer = LogAnalyzer(log_text)
                print("\n" + analyzer.format_report(errors_only=args.errors_only, last_cycle_only=args.last_cycle))
            else:
                print("[WARNUNG] LogAnalyzer Modul nicht verfügbar. Zeige Raw Logs:")
                print(log_text)
            return 0

    # Default: print logs
    print("\n--- Remote Logs ---")
    print(log_text)
    return 0


def cmd_config_diff(client: RemoteClient, args: argparse.Namespace) -> int:
    """Compares local user_config.json with the remote server version."""
    local_cfg_path = Path(args.local_config)
    if not local_cfg_path.exists():
        print(f"[FEHLER] Lokale Konfigurationsdatei existiert nicht: {local_cfg_path}", file=sys.stderr)
        return 1

    print(f"[*] Hole entfernte Konfiguration von {client.host}:{client.remote_path}/data/user_config.json...")
    res = client.run_ssh(f"cat {client.remote_path}/data/user_config.json")
    if res.returncode != 0:
        print(f"[FEHLER] Remote-Datei konnte nicht gelesen werden:\n{res.stderr}", file=sys.stderr)
        return res.returncode

    remote_text = res.stdout
    local_text = local_cfg_path.read_text(encoding="utf-8")

    # Format JSON nicely for comparison if valid
    try:
        remote_obj = json.loads(remote_text)
        local_obj = json.loads(local_text)
        norm_remote = json.dumps(remote_obj, indent=2, sort_keys=True)
        norm_local = json.dumps(local_obj, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[HINWEIS] JSON Parsing für strukturierten Diff fehlgeschlagen ({e}), verwende Plain Diff.")
        norm_remote = remote_text
        norm_local = local_text

    diff_lines = list(
        difflib.unified_diff(
            norm_remote.splitlines(),
            norm_local.splitlines(),
            fromfile=f"Server: {client.host}/data/user_config.json",
            tofile=f"Lokal: {local_cfg_path}",
            lineterm="",
        )
    )

    if not diff_lines:
        print("[OK] Keine Unterschiede! Lokale und Server-Konfiguration sind identisch.")
        return 0

    print("\n--- Konfigurations-Unterschiede (- Server, + Lokal) ---")
    for line in diff_lines:
        print(line)
    return 0


def cmd_config_pull(client: RemoteClient, args: argparse.Namespace) -> int:
    """Downloads remote user_config.json and updates local file."""
    local_cfg_path = Path(args.local_config)
    local_cfg_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[*] Lade Konfiguration von {client.host}:{client.remote_path}/data/user_config.json herunter...")

    # Backup local config if it exists
    if local_cfg_path.exists():
        backup_path = local_cfg_path.with_suffix(".json.local.bak")
        shutil.copy2(local_cfg_path, backup_path)
        print(f"[BACKUP] Lokale Konfiguration gesichert unter: {backup_path}")

    res = client.run_scp(
        f"{client.remote_path}/data/user_config.json",
        str(local_cfg_path),
        is_upload=False,
    )
    if res.returncode != 0:
        print(f"[FEHLER] Herunterladen fehlgeschlagen:\n{res.stderr}", file=sys.stderr)
        return res.returncode

    print(f"[OK] Remote user_config.json erfolgreich synchronisiert -> {local_cfg_path}")
    return 0


def cmd_config_push(client: RemoteClient, args: argparse.Namespace) -> int:
    """Validates local user_config.json and uploads it to the remote server."""
    local_cfg_path = Path(args.local_config)
    if not local_cfg_path.exists():
        print(f"[FEHLER] Lokale Konfigurationsdatei existiert nicht: {local_cfg_path}", file=sys.stderr)
        return 1

    # Validate JSON syntax
    try:
        content = local_cfg_path.read_text(encoding="utf-8")
        json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[FEHLER] Lokales user_config.json enthält ungültiges JSON: {e}", file=sys.stderr)
        return 1

    print("[OK] Lokales JSON erfolgreich validiert.")

    # Create remote backup
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_remote = f"{client.remote_path}/data/user_config.json.bak.{ts}"
    print(f"[*] Erstelle Server-Backup: {backup_remote}...")
    client.run_ssh(f"cp {client.remote_path}/data/user_config.json {backup_remote}")

    # Upload
    print(f"[*] Lade {local_cfg_path} nach {client.host}:{client.remote_path}/data/user_config.json hoch...")
    res = client.run_scp(
        str(local_cfg_path),
        f"{client.remote_path}/data/user_config.json",
        is_upload=True,
    )
    if res.returncode != 0:
        print(f"[FEHLER] Upload fehlgeschlagen:\n{res.stderr}", file=sys.stderr)
        return res.returncode

    print("[OK] user_config.json erfolgreich auf den Server hochgeladen.")

    if args.restart:
        print("[*] Führe Service-Neustart durch (--restart)...")
        return cmd_restart(client, args)

    return 0


def cmd_config_set(client: RemoteClient, args: argparse.Namespace) -> int:
    """Sets a specific configuration value (e.g. agriculture.auto_water=false)."""
    local_cfg_path = Path(args.local_config)
    if not local_cfg_path.exists():
        print(f"[FEHLER] Lokale Konfigurationsdatei nicht gefunden: {local_cfg_path}", file=sys.stderr)
        return 1

    key_path = args.key
    raw_val = args.val

    # Parse value type
    val: Any
    if raw_val.lower() == "true":
        val = True
    elif raw_val.lower() == "false":
        val = False
    elif raw_val.lower() in ("null", "none"):
        val = None
    else:
        try:
            val = int(raw_val)
        except ValueError:
            try:
                val = float(raw_val)
            except ValueError:
                try:
                    val = json.loads(raw_val)
                except Exception:
                    val = raw_val

    data = json.loads(local_cfg_path.read_text(encoding="utf-8"))
    parts = key_path.split(".")
    curr = data
    for part in parts[:-1]:
        if part not in curr or not isinstance(curr[part], dict):
            curr[part] = {}
        curr = curr[part]

    old_val = curr.get(parts[-1], "<nicht gesetzt>")
    curr[parts[-1]] = val

    local_cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[OK] '{key_path}' geändert: {old_val} -> {val}")

    if args.push:
        print("[*] Übertrage Änderung direkt zum Server (--push)...")
        return cmd_config_push(client, args)

    return 0


def cmd_config_get(client: RemoteClient, args: argparse.Namespace) -> int:
    """Inspects a configuration key or section."""
    local_cfg_path = Path(args.local_config)
    if not local_cfg_path.exists():
        print(f"[FEHLER] Lokale Konfigurationsdatei nicht gefunden: {local_cfg_path}", file=sys.stderr)
        return 1

    data = json.loads(local_cfg_path.read_text(encoding="utf-8"))
    key_path = getattr(args, "key", None)
    if not key_path:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    curr = data
    for part in key_path.split("."):
        if isinstance(curr, dict) and part in curr:
            curr = curr[part]
        else:
            print(f"[FEHLER] Schlüsselpfad '{key_path}' nicht gefunden (bei '{part}').", file=sys.stderr)
            return 1

    if isinstance(curr, (dict, list)):
        print(json.dumps(curr, indent=2, ensure_ascii=False))
    else:
        print(f"{key_path} = {curr}")
    return 0


def cmd_setup_ssh(client: RemoteClient, args: argparse.Namespace) -> int:
    """Assists in verifying or setting up SSH key access to the server."""
    print("=" * 60)
    print("  SSH-Schlüssel Einrichtungshilfe für Linux-Server")
    print("=" * 60)

    user_profile = os.environ.get("USERPROFILE", "")
    ssh_dir = Path(user_profile) / ".ssh" if user_profile else Path.home() / ".ssh"
    key_ed25519 = ssh_dir / "id_ed25519"
    key_rsa = ssh_dir / "id_rsa"

    found_key = None
    if key_ed25519.exists():
        found_key = key_ed25519
    elif key_rsa.exists():
        found_key = key_rsa

    if found_key:
        print(f"[OK] Lokaler SSH-Schlüssel gefunden: {found_key}")
        pub_key = found_key.with_suffix(".pub")
        if pub_key.exists():
            print(f"Öffentlicher Schlüssel: {pub_key}")
            print("\nFalls noch nicht auf dem Server hinterlegt, führen Sie in PowerShell aus:")
            print(f'Get-Content "{pub_key}" | ssh {client.user}@{client.host} "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"')
    else:
        print("[HINWEIS] Kein Standard-SSH-Schlüssel (id_ed25519 oder id_rsa) gefunden.")
        print("Erstellen Sie einen mit folgendem PowerShell-Befehl:")
        print("  ssh-keygen -t ed25519 -C 'antigravity-deploy'")
        print("\nÜbertragen Sie den Schlüssel anschließend auf den Server:")
        print(f'Get-Content "$env:USERPROFILE\\.ssh\\id_ed25519.pub" | ssh {client.user}@{client.host} "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"')

    print("\nTeste aktuelle Verbindung...")
    if client.test_connection():
        print("[ERFOLG] Verbindung zum Linux-Server funktioniert einwandfrei!")
        return 0
    else:
        print("[WARNUNG] Verbindung schlägt aktuell noch fehl (siehe Meldung oben).")
        return 1


def main() -> None:
    env_cfg = load_env_defaults()

    parser = argparse.ArgumentParser(
        description="pyFarm Linux Server Deployment & Operations Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--host", default=env_cfg.get("host", DEFAULT_HOST), help=f"Server Hostname/IP (Standard: {DEFAULT_HOST})")
    parser.add_argument("--user", default=env_cfg.get("user", DEFAULT_USER), help=f"SSH Benutzer (Standard: {DEFAULT_USER})")
    parser.add_argument("-p", "--port", type=int, default=int(env_cfg.get("port", DEFAULT_PORT)), help="SSH Port")
    parser.add_argument("--key", "-i", default=env_cfg.get("key"), help="Pfad zum privaten SSH-Schlüssel")
    parser.add_argument("--password", default=env_cfg.get("password"), help="SSH Passwort (oder MFF_DEPLOY_PASSWORD in .env)")
    parser.add_argument("--path", default=env_cfg.get("path", DEFAULT_REMOTE_PATH), help="Pfad zum Repository auf Server")
    parser.add_argument("--service", default=env_cfg.get("service", DEFAULT_SERVICE), help="Systemd Dienstname")
    parser.add_argument("--local-config", default=str(DEFAULT_LOCAL_CONFIG), help="Lokaler Pfad zu user_config.json")
    parser.add_argument("--interactive", action="store_true", help="Erlaubt interaktive Passwortabfrage via Terminal")

    subparsers = parser.add_subparsers(dest="command", required=True, help="Auszuführende Aktion")

    # status
    p_status = subparsers.add_parser("status", help="Prüft ob der pyFarm-Dienst läuft")
    p_status.set_defaults(func=cmd_status)

    # start
    p_start = subparsers.add_parser("start", help="Startet den pyFarm-Dienst")
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = subparsers.add_parser("stop", help="Stoppt den pyFarm-Dienst")
    p_stop.set_defaults(func=cmd_stop)

    # restart
    p_restart = subparsers.add_parser("restart", help="Startet den pyFarm-Dienst neu")
    p_restart.set_defaults(func=cmd_restart)

    # update / pull
    p_update = subparsers.add_parser("update", aliases=["pull"], help="Aktualisiert das Repository auf dem Server (git pull & uv sync)")
    p_update.add_argument("--restart", action="store_true", help="Startet den Dienst nach dem Update automatisch neu")
    p_update.add_argument("--skip-local-check", action="store_true", help="Überspringt den lokalen Git-Statuscheck")
    p_update.set_defaults(func=cmd_update)

    # logs
    p_logs = subparsers.add_parser("logs", help="Liest und analysiert Server-Logs")
    p_logs.add_argument("-n", "--lines", type=int, default=100, help="Anzahl der Zeilen (Standard: 100)")
    p_logs.add_argument("--journal", action="store_true", help="Liest aus systemd journalctl statt myfreefarm.log")
    p_logs.add_argument("--download", nargs="?", const="logs/server_myfreefarm.log", help="Speichert Log lokal (optional Zieldatei angeben)")
    p_logs.add_argument("--analyze", action="store_true", help="Führt automatische Log-Analyse aus")
    p_logs.add_argument("--errors-only", action="store_true", help="Zeigt bei Analyse nur Fehler")
    p_logs.add_argument("--last-cycle", action="store_true", help="Zeigt bei Analyse den letzten Zyklus")
    p_logs.set_defaults(func=cmd_logs)

    # config-diff
    p_cdiff = subparsers.add_parser("config-diff", help="Vergleicht lokale user_config.json mit der auf dem Server")
    p_cdiff.set_defaults(func=cmd_config_diff)

    # config-pull
    p_cpull = subparsers.add_parser("config-pull", help="Lädt user_config.json vom Server herunter")
    p_cpull.set_defaults(func=cmd_config_pull)

    # config-push
    p_cpush = subparsers.add_parser("config-push", help="Validiert und lädt lokale user_config.json auf den Server hoch")
    p_cpush.add_argument("--restart", action="store_true", help="Startet Dienst nach Upload neu")
    p_cpush.set_defaults(func=cmd_config_push)

    # config-set
    p_cset = subparsers.add_parser("config-set", help="Ändert einen Konfigurationswert (z.B. agriculture.auto_water true)")
    p_cset.add_argument("key", help="Schlüsselpfad, z.B. trade.enabled oder agriculture.plant_strategy")
    p_cset.add_argument("val", help="Neuer Wert (z.B. true, false, 500, plantQuest, '[\"v\"]')")
    p_cset.add_argument("--push", action="store_true", help="Überträgt die Änderung sofort auf den Server")
    p_cset.add_argument("--restart", action="store_true", help="Startet Dienst nach Übertragung neu")
    p_cset.set_defaults(func=cmd_config_set)

    # config-get
    p_cget = subparsers.add_parser("config-get", help="Liest einen Konfigurationswert oder Bereich")
    p_cget.add_argument("key", nargs="?", help="Optionaler Schlüsselpfad (z.B. agriculture oder trade.enabled)")
    p_cget.set_defaults(func=cmd_config_get)

    # setup-ssh
    p_ssh = subparsers.add_parser("setup-ssh", help="Hilfe & Verbindungstest für SSH-Schlüssel")
    p_ssh.set_defaults(func=cmd_setup_ssh)

    args = parser.parse_args()

    client = RemoteClient(
        host=args.host,
        user=args.user,
        port=args.port,
        remote_path=args.path,
        service=args.service,
        key_file=args.key,
        password=args.password,
        batch_mode=not args.interactive,
    )

    exit_code = args.func(client, args)
    sys.exit(exit_code or 0)


if __name__ == "__main__":
    main()
