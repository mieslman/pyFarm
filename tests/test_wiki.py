"""Automated test suite verifying the LLM-Wiki OKF compliance and link integrity."""

from pathlib import Path

import pytest

from scripts.lint_wiki import WikiLinter, lint_wiki


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def wiki_dir(repo_root: Path) -> Path:
    return repo_root / "llm-wiki"


def test_wiki_full_lint(wiki_dir: Path, repo_root: Path):
    """Ensure the entire LLM-Wiki passes all OKF linter rules with 0 errors."""
    error_count, _warning_count, errors, _warnings = lint_wiki(
        wiki_dir=wiki_dir,
        repo_root=repo_root,
    )
    assert error_count == 0, f"Wiki lint failed with {error_count} errors:\n" + "\n".join(errors)


def test_wiki_readme_index(wiki_dir: Path, repo_root: Path):
    """Ensure all markdown files in llm-wiki/ are indexed in 00-README.md."""
    linter = WikiLinter(wiki_dir=wiki_dir, repo_root=repo_root)
    all_files = sorted([f.name for f in wiki_dir.glob("*.md")])
    assert len(all_files) >= 20, "Expected at least 20 documentation files in llm-wiki/"

    file_frontmatters = {}
    for fname in all_files:
        content = (wiki_dir / fname).read_text(encoding="utf-8")
        fm = linter.check_frontmatter(fname, content)
        if fm:
            file_frontmatters[fname] = fm

    linter.check_readme_consistency(all_files, file_frontmatters)
    assert len(linter.errors) == 0, "README index consistency errors:\n" + "\n".join(linter.errors)
