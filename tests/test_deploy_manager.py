import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / ".agents" / "skills" / "deploy-linux" / "scripts"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import analyze_logs
import deploy_manager as deploy_mgr


def test_log_analyzer_parsing():
    sample_log = """2026-09-28 12:00:00.000 | INFO     | app.worker.scheduler:start:10 - Background started
2026-09-28 12:00:01.000 | INFO     | app.worker.scheduler:run_cycle:137 - ========== WorkerScheduler: Starte Zyklus für 'TestUser' auf Server 1 ==========
2026-09-28 12:00:02.000 | DEBUG    | app.modules.agriculture.service:serve:50 - Fields checked
2026-09-28 12:00:03.000 | WARNING  | app.modules.events.manager:serve:139 - EventManager: Warning message
2026-09-28 12:00:04.000 | ERROR    | app.modules.trade.service:trade:99 - Trade failed: timeout
2026-09-28 12:00:05.000 | INFO     | app.worker.scheduler:run_cycle:345 - ========== WorkerScheduler: Zyklus erfolgreich abgeschlossen ==========
2026-09-28 12:00:06.000 | INFO     | app.worker.scheduler:start:391 - WorkerScheduler: Nächster Zyklus in 600 Sekunden (~10 Min)...
"""
    analyzer = analyze_logs.LogAnalyzer(sample_log)
    summary = analyzer.get_summary()
    assert summary["total_entries"] == 7
    assert summary["error_count"] == 1
    assert summary["warning_count"] == 1

    cycles = analyzer.get_cycles()
    assert len(cycles) == 1
    c = cycles[0]
    assert c.username == "TestUser"
    assert c.server_id == 1
    assert c.completed is True
    assert c.duration_seconds == 4.0
    assert c.next_cycle_seconds == 600
    assert "agriculture" in c.modules
    assert len(c.errors) == 1
    assert len(c.warnings) == 1

    report = analyzer.format_report()
    assert "Trade failed" in report
    assert "EventManager: Warning message" in report


def test_remote_client_command_generation():
    client = deploy_mgr.RemoteClient(
        host="test.server.org",
        user="testuser",
        port=2222,
        remote_path="/srv/mff",
        service="mff_unit",
        key_file="/path/to/key.pem",
        batch_mode=True,
    )
    cmd = client._build_ssh_cmd("uptime", batch_mode=True)
    assert "ssh" in cmd
    assert "-i" in cmd
    assert "/path/to/key.pem" in cmd
    assert "-p" in cmd
    assert "2222" in cmd
    assert "testuser@test.server.org" in cmd
    assert "uptime" in cmd
    assert "BatchMode=yes" in " ".join(cmd)


def test_config_set_logic(tmp_path):
    test_cfg = tmp_path / "user_config.json"
    initial_data = {
        "agriculture": {
            "enabled": True,
            "min_products": 500
        }
    }
    test_cfg.write_text(json.dumps(initial_data), encoding="utf-8")

    class FakeArgs:
        local_config = str(test_cfg)
        key = "agriculture.min_products"
        val = "1000"
        push = False

    fake_client = deploy_mgr.RemoteClient()
    exit_code = deploy_mgr.cmd_config_set(fake_client, FakeArgs())
    assert exit_code == 0

    updated = json.loads(test_cfg.read_text(encoding="utf-8"))
    assert updated["agriculture"]["min_products"] == 1000


def test_config_push_invalid_json(tmp_path):
    test_cfg = tmp_path / "invalid_config.json"
    test_cfg.write_text("{\"invalid\": json,", encoding="utf-8")

    class FakeArgs:
        local_config = str(test_cfg)
        restart = False

    fake_client = deploy_mgr.RemoteClient()
    exit_code = deploy_mgr.cmd_config_push(fake_client, FakeArgs())
    assert exit_code == 1


def test_log_analyzer_with_traceback():
    sample_with_tb = """2026-09-28 12:00:00.000 | ERROR    | app.main:run:45 - An unhandled exception occurred!
Traceback (most recent call last):
  File "app/main.py", line 40, in run
    raise ValueError("Invalid state")
ValueError: Invalid state
2026-09-28 12:00:01.000 | INFO     | app.main:run:50 - Next action
"""
    analyzer = analyze_logs.LogAnalyzer(sample_with_tb)
    errors = analyzer.get_errors()
    assert len(errors) == 1
    assert "ValueError: Invalid state" in errors[0].message
    assert len(errors[0].raw_lines) == 5

