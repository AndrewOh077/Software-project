"""Week 0 smoke test: makes sure the shared files exist so CI is green from day one.

Real tests (gateway round robin, token bucket, MTTR calculator...) get added next to this file.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = [
    "README.md",
    "CONTRIBUTING.md",
    ".gitignore",
    ".env.example",
    "docs/contracts.md",
    "docs/decisions.md",
    "docs/team-plan.md",
]


def test_required_files_exist():
    missing = [f for f in REQUIRED_FILES if not (ROOT / f).exists()]
    assert not missing, f"Missing files: {missing}"


def test_env_is_ignored():
    gitignore = (ROOT / ".gitignore").read_text()
    assert ".env" in gitignore.splitlines(), ".env must be in .gitignore"
