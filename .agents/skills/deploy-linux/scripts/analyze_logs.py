#!/usr/bin/env python3
"""
MyFreeFarm Log Analyzer
Analyzes Loguru-formatted log files from pyFarm locally.
Extracts errors, warnings, cycle metrics, and module activities.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG_LINE_REGEX = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+\|\s+"
    r"(?P<level>[A-Z]+)\s*\|\s+"
    r"(?P<logger>[^:]+):(?P<func>[^:]+):(?P<line>\d+)\s+-\s+"
    r"(?P<message>.*)$"
)


@dataclass
class LogEntry:
    timestamp_str: str
    level: str
    logger: str
    function: str
    line_no: int
    message: str
    raw_lines: list[str] = field(default_factory=list)

    @property
    def timestamp(self) -> datetime | None:
        try:
            return datetime.strptime(self.timestamp_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
        except Exception:
            return None


@dataclass
class CycleInfo:
    cycle_num: int
    start_time: str
    end_time: str | None = None
    duration_seconds: float | None = None
    next_cycle_seconds: int | None = None
    username: str | None = None
    server_id: int | None = None
    modules: list[str] = field(default_factory=list)
    errors: list[LogEntry] = field(default_factory=list)
    warnings: list[LogEntry] = field(default_factory=list)
    completed: bool = False


class LogAnalyzer:
    def __init__(self, log_content: str):
        self.raw_content = log_content
        self.entries: list[LogEntry] = []
        self._parse()

    def _parse(self) -> None:
        current_entry: LogEntry | None = None
        for line in self.raw_content.splitlines():
            match = LOG_LINE_REGEX.match(line)
            if match:
                if current_entry:
                    self.entries.append(current_entry)
                current_entry = LogEntry(
                    timestamp_str=match.group("timestamp"),
                    level=match.group("level").strip(),
                    logger=match.group("logger").strip(),
                    function=match.group("func").strip(),
                    line_no=int(match.group("line")),
                    message=match.group("message"),
                    raw_lines=[line],
                )
            else:
                if current_entry:
                    current_entry.raw_lines.append(line)
                    current_entry.message += "\n" + line
                elif line.strip():
                    # Orphan line before first log header
                    current_entry = LogEntry(
                        timestamp_str="",
                        level="UNKNOWN",
                        logger="",
                        function="",
                        line_no=0,
                        message=line,
                        raw_lines=[line],
                    )

        if current_entry:
            self.entries.append(current_entry)

    def get_summary(self) -> dict[str, Any]:
        level_counts: dict[str, int] = {}
        for entry in self.entries:
            level_counts[entry.level] = level_counts.get(entry.level, 0) + 1

        first_ts = self.entries[0].timestamp_str if self.entries else None
        last_ts = self.entries[-1].timestamp_str if self.entries else None

        return {
            "total_entries": len(self.entries),
            "level_counts": level_counts,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "error_count": level_counts.get("ERROR", 0) + level_counts.get("CRITICAL", 0),
            "warning_count": level_counts.get("WARNING", 0),
        }

    def get_cycles(self) -> list[CycleInfo]:
        cycles: list[CycleInfo] = []
        current_cycle: CycleInfo | None = None

        cycle_start_re = re.compile(
            r"WorkerScheduler:\s+Starte Zyklus f[üu]r '(?P<user>[^']+)' auf Server (?P<server>\d+)"
        )
        cycle_end_re = re.compile(r"WorkerScheduler:\s+Zyklus erfolgreich abgeschlossen")
        next_cycle_re = re.compile(r"WorkerScheduler:\s+N[äa]chster Zyklus in (?P<sec>\d+)\s+Sekunden")

        cycle_count = 0
        for entry in self.entries:
            start_m = cycle_start_re.search(entry.message)
            if start_m:
                cycle_count += 1
                current_cycle = CycleInfo(
                    cycle_num=cycle_count,
                    start_time=entry.timestamp_str,
                    username=start_m.group("user"),
                    server_id=int(start_m.group("server")),
                )
                cycles.append(current_cycle)
                continue

            if not current_cycle:
                continue

            if entry.level in ("ERROR", "CRITICAL"):
                current_cycle.errors.append(entry)
            elif entry.level == "WARNING":
                current_cycle.warnings.append(entry)

            # Detect module actions
            if "app.modules." in entry.logger:
                mod_name = entry.logger.split("app.modules.")[1].split(".")[0]
                if mod_name not in current_cycle.modules:
                    current_cycle.modules.append(mod_name)

            if cycle_end_re.search(entry.message):
                current_cycle.end_time = entry.timestamp_str
                current_cycle.completed = True
                # Calculate duration
                try:
                    dt_start = datetime.strptime(current_cycle.start_time, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
                    dt_end = datetime.strptime(current_cycle.end_time, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
                    current_cycle.duration_seconds = round((dt_end - dt_start).total_seconds(), 2)
                except (ValueError, TypeError):
                    current_cycle.duration_seconds = None

            next_m = next_cycle_re.search(entry.message)
            if next_m:
                current_cycle.next_cycle_seconds = int(next_m.group("sec"))

        return cycles

    def get_errors(self, include_warnings: bool = False) -> list[LogEntry]:
        target_levels = ("ERROR", "CRITICAL") if not include_warnings else ("ERROR", "CRITICAL", "WARNING")
        return [e for e in self.entries if e.level in target_levels]

    def format_report(self, errors_only: bool = False, last_cycle_only: bool = False) -> str:
        lines: list[str] = []
        summary = self.get_summary()
        cycles = self.get_cycles()

        lines.append("=" * 70)
        lines.append("  pyFarm Logfile-Analyse Report")
        lines.append("=" * 70)
        lines.append(f"Gesamt Einträge:   {summary['total_entries']}")
        lines.append(f"Zeitspanne:        {summary['first_timestamp']}  bis  {summary['last_timestamp']}")
        lines.append(f"Log-Level Counts:  {summary['level_counts']}")
        lines.append(f"Fehler / Critical: {summary['error_count']}")
        lines.append(f"Warnungen:         {summary['warning_count']}")
        lines.append(f"Erkannte Zyklen:   {len(cycles)}")
        lines.append("-" * 70)

        # Cycles overview
        if cycles:
            lines.append("WorkerScheduler Zyklen (letzte 5):")
            for c in cycles[-5:]:
                status_symbol = "OK" if c.completed else "INCOMPLETE/FAILED"
                dur = f"{c.duration_seconds}s" if c.duration_seconds is not None else "n/a"
                next_in = f"in {c.next_cycle_seconds}s" if c.next_cycle_seconds else "n/a"
                lines.append(
                    f"  # {c.cycle_num:2d} | Start: {c.start_time} | Dauer: {dur:>6} | "
                    f"Status: {status_symbol:<10} | Fehler: {len(c.errors)} | Warnungen: {len(c.warnings)} | Nächster: {next_in}"
                )
                if c.modules:
                    lines.append(f"       Module: {', '.join(c.modules)}")
            lines.append("-" * 70)

        # Last cycle details if requested
        if last_cycle_only and cycles:
            last_c = cycles[-1]
            lines.append(f"Detailansicht letzter Zyklus (#{last_c.cycle_num}):")
            lines.append(f"  Benutzer:  {last_c.username} (Server {last_c.server_id})")
            lines.append(f"  Start:     {last_c.start_time}")
            lines.append(f"  Ende:      {last_c.end_time or 'Nicht beendet'}")
            lines.append(f"  Dauer:     {last_c.duration_seconds}s")
            lines.append(f"  Nächster:  {last_c.next_cycle_seconds}s")
            lines.append(f"  Module:    {', '.join(last_c.modules)}")
            lines.append("-" * 70)

        # Errors section
        errors = self.get_errors(include_warnings=not errors_only)
        if errors:
            lines.append(f"Gefundene Probleme ({len(errors)}):")
            for idx, err in enumerate(errors[-30:], 1):  # max 30 entries
                lines.append(f"[{idx:2d}] {err.timestamp_str} | {err.level:<7} | {err.logger}:{err.line_no}")
                # Print indented message
                for msg_line in err.message.splitlines():
                    lines.append(f"     {msg_line}")
                lines.append("")
        else:
            lines.append("Keine Fehler oder Warnungen gefunden! System läuft stabil.")

        lines.append("=" * 70)
        return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="MyFreeFarm Log-Analyse-Tool")
    parser.add_argument("logfile", nargs="?", default="logs/myfreefarm.log", help="Pfad zur Log-Datei")
    parser.add_argument("--errors-only", action="store_true", help="Nur echte Fehler (ERROR/CRITICAL) anzeigen")
    parser.add_argument("--warnings", action="store_true", help="Warnungen einbeziehen")
    parser.add_argument("--last-cycle", action="store_true", help="Detailansicht des letzten Scheduler-Zyklus")
    parser.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    parser.add_argument("--tail", type=int, default=0, help="Nur die letzten N Zeilen des Logs analysieren")

    args = parser.parse_args()

    log_path = Path(args.logfile)
    if not log_path.exists():
        print(f"Fehler: Logdatei nicht gefunden: {log_path}", file=sys.stderr)
        sys.exit(1)

    content = log_path.read_text(encoding="utf-8", errors="replace")
    if args.tail > 0:
        lines = content.splitlines()[-args.tail:]
        content = "\n".join(lines)

    analyzer = LogAnalyzer(content)

    if args.json:
        summary = analyzer.get_summary()
        cycles = [
            {
                "cycle_num": c.cycle_num,
                "start_time": c.start_time,
                "end_time": c.end_time,
                "duration_seconds": c.duration_seconds,
                "next_cycle_seconds": c.next_cycle_seconds,
                "username": c.username,
                "server_id": c.server_id,
                "modules": c.modules,
                "completed": c.completed,
                "error_count": len(c.errors),
                "warning_count": len(c.warnings),
            }
            for c in analyzer.get_cycles()
        ]
        errors = [
            {
                "timestamp": e.timestamp_str,
                "level": e.level,
                "logger": e.logger,
                "function": e.function,
                "line": e.line_no,
                "message": e.message,
            }
            for e in analyzer.get_errors(include_warnings=args.warnings)
        ]
        print(json.dumps({"summary": summary, "cycles": cycles, "issues": errors}, indent=2))
    else:
        print(analyzer.format_report(errors_only=args.errors_only, last_cycle_only=args.last_cycle))


if __name__ == "__main__":
    main()
