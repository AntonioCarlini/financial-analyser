"""Shared test helpers: fixture discovery and the canonical (format-independent) view."""
from __future__ import annotations

import json
import os
from dataclasses import fields, is_dataclass
from decimal import Decimal
from pathlib import Path

from statements_ingest.models import validate_account

FIXTURES = Path(__file__).parent / "fixtures"


def update_golden_requested() -> bool:
    return os.environ.get("INGEST_UPDATE_GOLDEN") == "1"


def fixture_cases(provider: str | None = None):
    """(provider, path) for every synthetic text fixture, sorted for stable ordering."""
    cases = []
    for pdir in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        if provider and pdir.name != provider:
            continue
        cases.extend((pdir.name, f) for f in sorted(pdir.glob("*.txt")))
    return cases


def _conv(o):
    if is_dataclass(o):
        return {f.name: _conv(getattr(o, f.name)) for f in fields(o)}
    if isinstance(o, Decimal):
        return format(o.normalize(), "f")
    if isinstance(o, (list, tuple)):
        return [_conv(x) for x in o]
    return o


def canonical(doc) -> dict:
    """What an adapter produced, in plain JSON-able form, plus the hard-check verdict.

    This is the stable contract between adapters and everything downstream. Tests pin
    it, so the CSV layout can change without touching parser tests.
    """
    d = _conv(doc)
    for obj, as_dict in zip(doc.accounts, d["accounts"]):
        as_dict["problems"] = validate_account(obj)
    return d


def dump(d: dict) -> str:
    return json.dumps(d, indent=2, sort_keys=True) + "\n"
