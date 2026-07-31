"""Create timestamped SQLite backups and prune expired backup files."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from data.database import DatabaseManager


def prune_backups(directory: str | Path, *, retention_days: int, now: float | None = None) -> list[Path]:
    """Delete ``*.db`` backups older than ``retention_days`` and return them.

    Directories and unrelated files are never touched.  A supplied ``now``
    makes retention behavior deterministic for callers and tests.
    """
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    root = Path(directory)
    if not root.exists():
        return []
    cutoff = (time.time() if now is None else now) - retention_days * 86_400
    removed: list[Path] = []
    for candidate in root.glob("*.db"):
        if candidate.is_file() and candidate.stat().st_mtime < cutoff:
            candidate.unlink()
            removed.append(candidate)
    return removed


def backup_database(database_path: str | Path, backup_dir: str | Path, *, retention_days: int = 14, now: float | None = None) -> Path:
    """Take a consistent SQLite backup, then enforce the retention policy."""
    database_path = Path(database_path)
    destination_dir = Path(backup_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now))
    destination = destination_dir / f"{database_path.stem}-{timestamp}.db"
    db = DatabaseManager(database_path)
    try:
        db.backup(destination)
    finally:
        db.close()
    prune_backups(destination_dir, retention_days=retention_days, now=now)
    return destination


def main() -> int:
    """Run the backup command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/trading.db")
    parser.add_argument("--backup-dir", default="data/backups")
    parser.add_argument("--retention-days", type=int, default=14)
    args = parser.parse_args()
    print(backup_database(args.database, args.backup_dir, retention_days=args.retention_days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
