import subprocess
from datetime import datetime
from pathlib import Path


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=5, check=True,
    )
    return result.stdout.strip()


def build_version() -> str:
    """Short commit ID and commit time of the running code, e.g. '3fa9c1e · 2026-11-23 12:10'."""
    try:
        commit, _, stamp = _git("log", "-1", "--format=%h%x09%cI").partition("\t")
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
        when = datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return f"{commit}{'+' if dirty else ''} · {when}" if commit else ""
