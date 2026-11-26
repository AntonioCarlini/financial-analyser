"""CLI: python -m statements_ingest STATEMENTS_DIR --store STORE_DIR --person AB"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from .models import validate_account
from .providers import PROVIDERS
from .store import Store, StoreError, find_gaps, find_range_gaps


def prepare(sf, acct, parser, person, label_override, ref_override):
    """Cross-check hints against contents. Returns (account_ref, label, problems)."""
    problems = list(acct.warnings)
    ref = acct.account_ref or ref_override
    if ref is None:
        problems.append("no account reference in the statement; supply one with --account-ref")
    elif acct.account_ref and ref_override and acct.account_ref != ref_override:
        problems.append("account reference in the statement differs from --account-ref")
    if sf.account_type and acct.account_type and acct.account_type != sf.account_type:
        problems.append(f"filename says {sf.account_type} but statement says {acct.account_type}")
    if sf.year is not None and acct.as_of and acct.as_of[:7] != f"{sf.year}-{sf.month:02d}":
        problems.append(f"filename says {sf.year}-{sf.month:02d} but statement says {acct.as_of[:7]}")
    if not problems:
        problems = validate_account(acct)
    label = label_override or f"{parser.name.title()} {acct.account_type}"
    return ref, label, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m statements_ingest", description=__doc__)
    ap.add_argument("statements_dir", type=Path, help="directory containing statement PDFs")
    ap.add_argument("--store", type=Path, required=True,
                    help="directory holding the consolidated CSVs (created if missing)")
    ap.add_argument("--person", required=True, help="owner's initials, e.g. AB")
    ap.add_argument("--store-prefix", help="file name prefix (default: the person's initials). "
                    "Use one prefix for several people to keep them in the same files.")
    ap.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    ap.add_argument("--account-ref", help="account reference: used when a statement doesn't "
                    "print one, and checked against it when it does (single-account runs)")
    ap.add_argument("--account-label", help="free-text label stored with new rows")
    ap.add_argument("--allow-update", action="store_true",
                    help="overwrite stored data whose parsed content differs")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; write nothing")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9]{1,10}", args.person):
        ap.error("--person should be short initials (letters/digits only)")
    person = args.person.upper()
    prefix = args.store_prefix or person
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", prefix):
        ap.error("--store-prefix may only contain letters, digits, '-' and '_'")
    if not args.statements_dir.is_dir():
        print(f"Not a directory: {args.statements_dir}", file=sys.stderr)
        return 1

    parser = PROVIDERS[args.provider]()
    try:
        store = Store(args.store, prefix)
    except StoreError as e:
        print(f"Store problem: {e}", file=sys.stderr)
        return 1

    files, ignored = parser.discoverer.discover(args.statements_dir)
    tally, bad, check_warnings = {}, 0, 0
    for sf in files:
        try:
            doc = parser.read(sf.path)
        except Exception as e:  # unreadable/corrupt file: report and carry on
            print(f"REJECTED  {sf.path.name}\n            could not read: {e}")
            bad += 1
            continue
        sha = hashlib.sha256(sf.path.read_bytes()).hexdigest()
        prov = {"source_file": sf.path.name, "source_sha256": sha, "parser_version": parser.version}
        if not doc.accounts or doc.warnings:
            print(f"REJECTED  {sf.path.name}")
            for w in doc.warnings:
                print(f"            {w}")
            bad += 1
            continue
        for acct in doc.accounts:
            ref, label, problems = prepare(sf, acct, parser, person, args.account_label, args.account_ref)
            tag = f"{acct.account_type or '?'}"
            if problems:
                print(f"REJECTED  {sf.path.name}  [{tag}]")
                for p in problems:
                    print(f"            {p}")
                bad += 1
                continue
            ctx = {"provider": parser.name, "person": person, "account_ref": ref,
                   "account_type": acct.account_type, "account_label": label,
                   "source_type": doc.source_type}
            status, counts, differing = store.add_account(ctx, acct, prov, args.allow_update)
            tally[status] = tally.get(status, 0) + 1
            added = ", ".join(f"{k} {v}" for k, v in counts.items() if v)
            extra = f" (differs in: {', '.join(differing)})" if differing else ""
            print(f"{status:<9} {sf.path.name}  [{tag}]  {added}{extra}")
            if status == "CONFLICT":
                print("            use --allow-update to replace the stored version")
                bad += 1
            for n in acct.notes:
                print(f"            check: {n}")
                check_warnings += 1

    for path, reason in ignored:
        print(f"IGNORED   {path.name}: {reason}")

    if store.dirty and not args.dry_run:
        store.save()
        print(f"\nWrote {args.store} ({prefix}_*.csv)")
    elif store.dirty:
        print("\nDry run: nothing written")
    print("Summary: " + (", ".join(f"{k.lower()} {v}" for k, v in sorted(tally.items())) or "nothing to do")
          + (f", {bad} problem(s)" if bad else "")
          + (f", {check_warnings} check warning(s)" if check_warnings else ""))

    mine = lambda rows: [r for r in rows if r["provider"] == parser.name and r["person"] == person]
    if parser.coverage == "monthly":
        gaps = find_gaps(mine(store.snapshots.values()))
        label = "Missing statements"
    else:
        gaps = find_range_gaps(mine(store.totals.values()))
        label = "Dates not covered by any statement"
    for (prov_, who, ref), items in gaps.items():
        print(f"{label} for {who} {prov_} account {ref}: {', '.join(items)}")
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
