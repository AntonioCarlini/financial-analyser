"""Finding statement files. Deliberately separate so the layout can change.

A discoverer takes a directory and returns (statement files, ignored files).
To support a new naming scheme or folder layout, add another class with the
same discover() method and select it in __main__.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import normalise_account_type


@dataclass(frozen=True)
class StatementFile:
    path: Path
    account_type: str   # canonical, from the filename
    year: int
    month: int


class FilenameDiscoverer:
    """Single directory of files named like GIA-Statement-2026-10.pdf."""

    PATTERN = re.compile(
        r"^(?P<type>[A-Za-z]+)-Statement-(?P<year>\d{4})-(?P<month>\d{2})\.pdf$", re.I
    )

    def discover(self, directory: Path):
        files, ignored = [], []
        for path in sorted(Path(directory).iterdir()):
            if path.name.startswith(".") or not path.is_file():
                continue
            m = self.PATTERN.match(path.name)
            if not m:
                ignored.append((path, "filename does not match <TYPE>-Statement-YYYY-MM.pdf"))
                continue
            account_type = normalise_account_type(m["type"])
            month = int(m["month"])
            if account_type is None:
                ignored.append((path, f"unrecognised account type {m['type']!r}"))
            elif not 1 <= month <= 12:
                ignored.append((path, f"invalid month {m['month']!r}"))
            else:
                files.append(StatementFile(path, account_type, int(m["year"]), month))
        files.sort(key=lambda f: (f.year, f.month, f.account_type))
        return files, ignored
