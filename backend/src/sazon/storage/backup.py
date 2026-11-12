"""Online SQLite backups: the same page-by-page copy as the `sqlite3` shell's `.backup`, safe
while the API writes. `deploy/backup.sh` runs it inside the api container, as the app's user."""

import sqlite3
from pathlib import Path

from sqlalchemy import make_url


class BackupError(RuntimeError):
    pass


def sqlite_path(url: str) -> Path:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, "", ":memory:"):
        raise BackupError(f"Only a SQLite file can be backed up this way, not {parsed.drivername}")
    return Path(parsed.database)


def backup(url: str, dest: Path) -> int:
    """Copy the database at `url` to `dest` and check the copy. Returns its size in bytes."""
    source_path = sqlite_path(url)
    if not source_path.exists():
        raise BackupError(f"No database at {source_path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(partial)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
        result = target.execute("PRAGMA integrity_check").fetchone()
    finally:
        target.close()
        source.close()
    if result is None or result[0] != "ok":
        partial.unlink(missing_ok=True)
        raise BackupError(f"The copy failed its integrity check: {result}")
    partial.replace(dest)
    return dest.stat().st_size
