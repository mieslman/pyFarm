#!/usr/bin/env python3
"""LLM-Wiki Linter for pyFarm (Open Knowledge Format / OKF Validator).

Validates:
1. OKF Frontmatter schema (title, author, date, type, description, tags).
2. Index consistency with 00-README.md.
3. Internal cross-document Markdown links.
4. Relative and absolute repository file links.
5. Markdown formatting (H1 title, balanced code fences, no legacy path artifacts).
"""

import argparse
import contextlib
import re
import sys
from pathlib import Path
from typing import Any

import yaml

VALID_TYPES = {"architecture", "module", "code", "meta", "plan"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class WikiLinter:
    def __init__(self, wiki_dir: Path, repo_root: Path):
        self.wiki_dir = wiki_dir
        self.repo_root = repo_root
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def log_error(self, filename: str, message: str) -> None:
        self.errors.append(f"[{filename}] ERROR: {message}")

    def log_warning(self, filename: str, message: str) -> None:
        self.warnings.append(f"[{filename}] WARNING: {message}")

    def check_frontmatter(self, fname: str, content: str) -> dict[str, Any] | None:
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not fm_match:
            self.log_error(fname, "Fehlender YAML-Frontmatter (muss mit '---' beginnen und enden).")
            return None

        raw_yaml = fm_match.group(1)
        try:
            data = yaml.safe_load(raw_yaml)
        except Exception as e:
            self.log_error(fname, f"Ungültiges YAML im Frontmatter: {e}")
            return None

        if not isinstance(data, dict):
            self.log_error(fname, "Frontmatter ist kein gültiges YAML-Mapping (Key-Value-Paare).")
            return None

        required_keys = ["title", "author", "date", "type", "description", "tags"]
        for key in required_keys:
            if key not in data or data[key] is None or data[key] == "":
                self.log_error(fname, f"Fehlendes Pflichtfeld im Frontmatter: '{key}'.")

        # Validate date format YYYY-MM-DD
        if "date" in data:
            date_str = str(data["date"]).strip()
            if not DATE_PATTERN.match(date_str):
                self.log_error(
                    fname, f"Ungültiges Datumsformat '{date_str}' (erwartet YYYY-MM-DD)."
                )

        # Validate type
        doc_type = data.get("type")
        if doc_type and doc_type not in VALID_TYPES:
            self.log_error(
                fname,
                f"Ungültiger Dokumenttyp '{doc_type}'. Erlaubte Typen: {sorted(VALID_TYPES)}.",
            )

        # Validate tags
        tags = data.get("tags")
        if tags is not None:
            if not isinstance(tags, list) or len(tags) == 0:
                self.log_error(
                    fname, "Feld 'tags' muss eine nicht-leere Liste von Schlagwörtern sein."
                )
            elif not all(isinstance(t, str) and t.strip() for t in tags):
                self.log_error(fname, "Alle Tags müssen nicht-leere Strings sein.")

        return data

    def check_markdown_structure(self, fname: str, content: str) -> None:
        # Check for first H1 header
        body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL).strip()
        first_line = body.splitlines()[0] if body.splitlines() else ""
        if not first_line.startswith("# "):
            self.log_error(
                fname,
                f"Erste Zeile nach Frontmatter muss eine H1-Überschrift ('# Titel') sein. Gefunden: '{first_line}'.",
            )

        # Check balanced code fences
        fences = len(re.findall(r"```", content))
        if fences % 2 != 0:
            self.log_error(
                fname, f"Ungeschlossene Code-Fences (ungerade Anzahl von '```': {fences})."
            )

        # Check for legacy path artifacts
        legacy_tokens = ["myfreefarm/myfreefarm", "myfreefarm_python"]
        for line_no, line in enumerate(content.splitlines(), 1):
            for token in legacy_tokens:
                if token in line:
                    self.log_error(
                        fname,
                        f"Zeile {line_no}: Enthält veralteten Pfad/Token '{token}': {line.strip()}",
                    )

    def check_links(self, fname: str, content: str) -> None:
        md_links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
        for _, link in md_links:
            clean_link = link.split("#")[0].strip()
            if not clean_link or clean_link.startswith(("http://", "https://", "mailto:")):
                continue

            if clean_link.endswith(".md"):
                # Internal wiki link
                target_path = self.wiki_dir / clean_link
                if not target_path.exists():
                    self.log_error(
                        fname,
                        f"Gebrochener interner Wiki-Link auf nicht existierende Datei: '{clean_link}'.",
                    )
            elif clean_link.startswith("file:///"):
                # Absolute file link
                raw_path = clean_link.replace("file:///", "").replace("/", "\\")
                target_path = Path(raw_path)
                if not target_path.exists():
                    self.log_error(fname, f"Gebrochener lokaler Datei-Link: '{clean_link}'.")
            else:
                # Relative link to codebase
                target_path = (self.wiki_dir / clean_link).resolve()
                if not target_path.exists():
                    self.log_error(
                        fname,
                        f"Gebrochener relativer Dateipfad: '{clean_link}' -> aufgelöst zu '{target_path}'.",
                    )

    def check_readme_consistency(
        self, all_files: list[str], file_frontmatters: dict[str, dict[str, Any]]
    ) -> None:
        readme_file = self.wiki_dir / "00-README.md"
        if not readme_file.exists():
            self.log_error(
                "00-README.md", "Zentrale Inhaltsverzeichnis-Datei '00-README.md' existiert nicht!"
            )
            return

        readme_text = readme_file.read_text(encoding="utf-8")
        readme_entries = set(re.findall(r"\[(\d{2}-[\w-]+\.md)\]", readme_text))

        # Check for missing files in README
        for f in all_files:
            if f not in readme_entries:
                self.log_error(
                    "00-README.md",
                    f"Datei '{f}' ist nicht im Inhaltsverzeichnis von 00-README.md aufgeführt.",
                )

        # Check for non-existent files listed in README
        for entry in readme_entries:
            if not (self.wiki_dir / entry).exists():
                self.log_error(
                    "00-README.md",
                    f"Inhaltsverzeichnis verlinkt auf nicht existierende Datei '{entry}'.",
                )

        # Validate types in table match frontmatter types
        table_rows = re.findall(
            r"\|\s*\*\*\[(\d{2}-[\w-]+\.md)\]\([^\)]+\)\*\*\s*\|\s*`([^`]+)`\s*\|", readme_text
        )
        for doc_name, listed_type in table_rows:
            fm = file_frontmatters.get(doc_name)
            if fm and fm.get("type") != listed_type:
                self.log_error(
                    "00-README.md",
                    f"Typ-Konflikt für '{doc_name}': Tabelle listet '`{listed_type}`', aber Frontmatter hat '`{fm.get('type')}`'.",
                )

    def run(self) -> tuple[int, int]:
        md_files = sorted([f.name for f in self.wiki_dir.glob("*.md")])
        if not md_files:
            self.log_error(
                "wiki", f"Keine Markdown-Dateien im Verzeichnis '{self.wiki_dir}' gefunden."
            )
            return len(self.errors), len(self.warnings)

        file_frontmatters: dict[str, dict[str, Any]] = {}

        for fname in md_files:
            content = (self.wiki_dir / fname).read_text(encoding="utf-8")
            fm = self.check_frontmatter(fname, content)
            if fm:
                file_frontmatters[fname] = fm
            self.check_markdown_structure(fname, content)
            self.check_links(fname, content)

        self.check_readme_consistency(md_files, file_frontmatters)
        return len(self.errors), len(self.warnings)


def lint_wiki(
    wiki_dir: Path | None = None, repo_root: Path | None = None
) -> tuple[int, int, list[str], list[str]]:
    """Programmatic entry point for pytest and scripts."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    if wiki_dir is None:
        wiki_dir = repo_root / "llm-wiki"

    linter = WikiLinter(wiki_dir=wiki_dir, repo_root=repo_root)
    error_count, warning_count = linter.run()
    return error_count, warning_count, linter.errors, linter.warnings


def main() -> int:
    # Ensure UTF-8 output on Windows consoles if supported
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Lint LLM-Wiki in OKF format.")
    parser.add_argument(
        "--wiki-dir",
        type=Path,
        default=None,
        help="Pfad zum llm-wiki Verzeichnis (Standard: repo_root/llm-wiki)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    wiki_dir = args.wiki_dir or (repo_root / "llm-wiki")

    print(f"\n[LINT] Starte LLM-Wiki Linter fuer: {wiki_dir}")
    print("=" * 70)

    error_count, warning_count, errors, warnings = lint_wiki(wiki_dir=wiki_dir, repo_root=repo_root)

    for w in warnings:
        print(f"[WARN]  {w}")

    for e in errors:
        print(f"[ERROR] {e}")

    print("=" * 70)
    all_files = list(wiki_dir.glob("*.md"))
    if error_count == 0:
        print(
            f"[OK] Wiki-Lint erfolgreich! {len(all_files)} Dokumente geprueft, 0 Fehler, {warning_count} Warnungen.\n"
        )
        return 0
    else:
        print(
            f"[FAIL] Wiki-Lint fehlgeschlagen: {error_count} Fehler in {len(all_files)} Dokumenten gefunden.\n"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
