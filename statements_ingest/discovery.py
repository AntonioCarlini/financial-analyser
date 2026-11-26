"""Finding statement files. Deliberately separate so layouts can change.

A discoverer takes a directory and returns (source files, ignored files).
To support a new naming scheme or folder layout, add a class with the same
discover() method and point the provider's `discoverer` attribute at it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import normalise_account_type


@dataclass(frozen=True)
class SourceFile:
    path: Path
    source_type: str
    # Optional hints, cross-checked against the document's contents when present.
    account_type: str | None = None
    year: int | None = None
    month: int | None = None


def _candidates(directory: Path):
    for path in sorted(Path(directory).iterdir()):
        if path.is_file() and not path.name.startswith("."):
            yield path


class MonthlyFilenameDiscoverer:
    """Single directory of files named like GIA-Statement-2026-10.pdf."""

    PATTERN = re.compile(
        r"^(?P<type>[A-Za-z]+)-Statement-(?P<year>\d{4})-(?P<month>\d{2})\.pdf$", re.I
    )

    def discover(self, directory: Path):
        files, ignored = [], []
        for path in _candidates(directory):
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
                files.append(SourceFile(path, "statement", account_type, int(m["year"]), month))
        files.sort(key=lambda f: (f.year, f.month, f.account_type))
        return files, ignored


class ActivityStatementDiscoverer:
    """Files named like Activity-Statement-2025-04-06-2026-04-05.pdf.

    Dates are read from inside the document, so the name only has to start
    with Activity-Statement.
    """

    PATTERN = re.compile(r"^Activity-Statement.*\.pdf$", re.I)

    def discover(self, directory: Path):
        files, ignored = [], []
        for path in _candidates(directory):
            if self.PATTERN.match(path.name):
                files.append(SourceFile(path, "activity_statement"))
            else:
                ignored.append((path, "filename does not match Activity-Statement*.pdf"))
        return files, ignored
