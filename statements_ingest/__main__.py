"""CLI: python -m statements_ingest STATEMENTS_DIR --store STORE_DIR --person JD"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from .discovery import FilenameDiscoverer
from .models import Snapshot, month_end, validate
from .providers import PROVIDERS
from .store import Store, StoreError, find_gaps


def build_snapshot(sf, parsed, parser, person, label_override, sha256, account_ref_override=None):
    """Cross-check filename against statement contents and build a Snapshot.

    Returns (snapshot or None, problems).
    """
    problems = list(parsed.warnings)
    account_ref = parsed.account_ref or account_ref_override
    if account_ref is None:
        problems.append("no account reference in the statement; supply one with --account-ref")
    elif parsed.account_ref and account_ref_override and parsed.account_ref != account_ref_override:
        problems.append("account reference in the statement differs from --account-ref")
    if parsed.account_type and parsed.account_type != sf.account_type:
        problems.append(f"filename says {sf.account_type} but statement says {parsed.account_type}")
    if parsed.year is not None and (parsed.year, parsed.month) != (sf.year, sf.month):
        problems.append(
            f"filename says {sf.year}-{sf.month:02d} but statement says {parsed.year}-{parsed.month:02d}"
        )
    if problems:
        return None, problems

    snap = Snapshot(
        provider=parser.name, person=person, account_type=sf.account_type,
        account_label=label_override or f"{parser.name.title()} {sf.account_type}",
        account_ref=account_ref, period_start=None,
        period_end=month_end(sf.year, sf.month), currency=parsed.currency,
        portfolio_reported=parsed.portfolio, holdings_reported=parsed.holdings,
        cash_reported=parsed.cash, source_file=sf.path.name, source_sha256=sha256,
        parser_version=parser.version, positions=parsed.positions,
    )
    problems = validate(snap)
    return (None if problems else snap), problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m statements_ingest", description=__doc__)
    ap.add_argument("statements_dir", type=Path, help="directory containing statement PDFs")
    ap.add_argument("--store", type=Path, required=True,
                    help="directory holding the consolidated CSVs (created if missing)")
    ap.add_argument("--person", required=True, help="owner's initials, e.g. JD")
    ap.add_argument("--provider", default="freetrade", choices=sorted(PROVIDERS))
    ap.add_argument("--account-ref",
                    help="account reference: used when a statement doesn't print one, "
                         "and checked against it when it does")
    ap.add_argument("--account-label", help="free-text label stored with new rows")
    ap.add_argument("--allow-update", action="store_true",
                    help="overwrite stored statements whose parsed content differs")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; write nothing")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9]{1,10}", args.person):
        ap.error("--person should be short initials (letters/digits only)")
    person = args.person.upper()
    if not args.statements_dir.is_dir():
        print(f"Not a directory: {args.statements_dir}", file=sys.stderr)
        return 1

    parser = PROVIDERS[args.provider]()
    try:
        store = Store(args.store)
    except StoreError as e:
        print(f"Store problem: {e}", file=sys.stderr)
        return 1

    files, ignored = FilenameDiscoverer().discover(args.statements_dir)
    counts, bad = {}, 0
    for sf in files:
        try:
            parsed = parser.read(sf.path)
        except Exception as e:  # unreadable/corrupt file: report and carry on
            print(f"REJECTED  {sf.path.name}\n            could not read: {e}")
            bad += 1
            continue
        sha = hashlib.sha256(sf.path.read_bytes()).hexdigest()
        snap, problems = build_snapshot(sf, parsed, parser, person, args.account_label, sha,
                                        args.account_ref)
        if snap is None:
            print(f"REJECTED  {sf.path.name}")
            for p in problems:
                print(f"            {p}")
            bad += 1
            continue
        status, differing = store.upsert(snap, args.allow_update)
        counts[status] = counts.get(status, 0) + 1
        detail = f" (differs in: {', '.join(differing)})" if differing else ""
        print(f"{status:<9} {sf.path.name}{detail}")
        if status == "CONFLICT":
            print("            use --allow-update to replace the stored version")
            bad += 1

    for path, reason in ignored:
        print(f"IGNORED   {path.name}: {reason}")

    if store.dirty and not args.dry_run:
        store.save()
        print(f"\nWrote {args.store}")
    elif store.dirty:
        print("\nDry run: nothing written")
    print("Summary: " + (", ".join(f"{k.lower()} {v}" for k, v in sorted(counts.items())) or "nothing to do")
          + (f", {bad} problem(s)" if bad else ""))

    mine = [s for s in store.snapshots.values() if s.provider == parser.name and s.person == person]
    for (prov, who, ref), months in find_gaps(mine, parser.statement_interval_months).items():
        print(f"Missing statements for {who} {prov} account {ref}: {', '.join(months)}")
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
