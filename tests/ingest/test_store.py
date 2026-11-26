"""Store tests: CSV semantics (merge, order, idempotence, isolation), not parser behaviour.

These build accounts directly from the model classes so they do not depend on any adapter.
They assert properties (sorted, unchanged, replaced) rather than exact file text, so a
deliberate change of column layout only needs the column lists in store.py updating.
"""
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

from statements_ingest.models import ParsedAccount, PeriodTotal, Position, Transaction
from statements_ingest.providers import PROVIDERS
from statements_ingest.store import Store, StoreError, find_gaps, find_range_gaps

from .support import fixture_cases

PROV = {"source_file": "f.pdf", "source_sha256": "0" * 64, "parser_version": "1"}


def ctx(provider="prov", person="AB", ref="R1", source_type="statement", account_type="GIA"):
    return {"provider": provider, "person": person, "account_ref": ref,
            "account_type": account_type, "account_label": "label", "source_type": source_type}


def account(as_of="2026-10-31", cash="100.00", positions=(), totals=(), txns=(), start=None, end=None):
    return ParsedAccount(
        account_type="GIA", account_ref="R1", as_of=as_of, currency="GBP",
        portfolio=D(cash) + sum((p.value for p in positions), D(0)),
        holdings=sum((p.value for p in positions), D(0)), cash=D(cash),
        period_start=start, period_end=end,
        positions=list(positions), totals=list(totals), transactions=list(txns))


def pos(name="Fund A", qty="10", value="500.00"):
    return Position(instrument_name=name, ticker="FA", quantity=D(qty), value=D(value), currency="GBP")


def deposit(ts="2026-01-01T09:00:00Z", amount="100.00"):
    return Transaction(timestamp=ts, kind="deposit", raw_type="Bank", amount=D(amount), currency="GBP")


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "store"

    def store(self, prefix="AB"):
        return Store(self.dir, prefix)

    def files(self, prefix="AB"):
        return {p.name: p.read_bytes() for p in self.dir.glob(f"{prefix}_*.csv")}


class MergeTests(StoreTestCase):
    def test_round_trip_preserves_everything(self):
        s = self.store()
        acct = account(positions=[pos()], totals=[PeriodTotal("deposits", D("100.00"), "GBP")],
                       txns=[deposit()], start="2026-01-01T00:00:00Z", end="2026-10-31T23:59:00Z")
        self.assertEqual("ADDED", s.add_account(ctx(), acct, PROV, False)[0])
        s.save()
        again = self.store()
        self.assertEqual(s.snapshots, again.snapshots)
        self.assertEqual(dict(s.positions), dict(again.positions))
        self.assertEqual(s.totals, again.totals)
        self.assertEqual(s.txns, again.txns)

    def test_rerun_is_idempotent_down_to_the_bytes(self):
        s = self.store()
        acct = account(positions=[pos()], txns=[deposit()])
        s.add_account(ctx(), acct, PROV, False)
        s.save()
        before = self.files()
        s2 = self.store()
        self.assertEqual("UNCHANGED", s2.add_account(ctx(), acct, PROV, False)[0])
        self.assertFalse(s2.dirty)
        s2.save()
        self.assertEqual(before, self.files())

    def test_provenance_differences_alone_do_not_count_as_changes(self):
        s = self.store()
        s.add_account(ctx(), account(), PROV, False)
        other = {**PROV, "source_file": "renamed.pdf", "parser_version": "2"}
        self.assertEqual("UNCHANGED", s.add_account(ctx(), account(), other, False)[0])

    def test_rows_are_date_ordered_whatever_the_arrival_order(self):
        s = self.store()
        for month_end in ("2026-10-31", "2026-08-31", "2026-09-30", "2024-02-29"):
            s.add_account(ctx(), account(as_of=month_end), PROV, False)
        s.save()
        dates = [r["as_of"] for r in self.store().snapshots.values()]
        lines = (self.dir / "AB_snapshots.csv").read_text().splitlines()[1:]
        written = [ln.split(",")[6] for ln in lines]
        self.assertEqual(sorted(dates), written)

    def test_changed_content_is_a_conflict_until_updates_are_allowed(self):
        s = self.store()
        s.add_account(ctx(), account(cash="100.00", positions=[pos("Old Fund")]), PROV, False)
        newer = account(cash="150.00", positions=[pos("New Fund")])
        status, _, differing = s.add_account(ctx(), newer, PROV, False)
        self.assertEqual("CONFLICT", status)
        self.assertEqual(["snapshot/positions"], differing)
        key = next(iter(s.snapshots))
        self.assertEqual(D("100.00"), s.snapshots[key]["cash_reported"])        # untouched
        self.assertEqual("UPDATED", s.add_account(ctx(), newer, PROV, True)[0])
        self.assertEqual(D("150.00"), s.snapshots[key]["cash_reported"])
        self.assertEqual(["New Fund"], [p["instrument_name"] for p in s.positions[key]])  # old row gone

    def test_overlapping_documents_do_not_duplicate_transactions(self):
        s = self.store()
        a = account(txns=[deposit("2026-01-01T09:00:00Z"), deposit("2026-02-01T09:00:00Z")])
        b = account(as_of="2026-12-31", txns=[deposit("2026-02-01T09:00:00Z"), deposit("2026-03-01T09:00:00Z")])
        s.add_account(ctx(), a, PROV, False)
        status, counts, _ = s.add_account(ctx(), b, PROV, False)
        self.assertEqual(1, counts["transactions"])      # only the genuinely new one
        self.assertEqual(3, len(s.txns))

    def test_identical_rows_within_one_document_are_both_kept(self):
        s = self.store()
        s.add_account(ctx(), account(txns=[deposit(), deposit()]), PROV, False)
        self.assertEqual(2, len(s.txns))

    def test_people_and_accounts_never_collide(self):
        s = self.store()
        for person, ref in (("AB", "R1"), ("CD", "R1"), ("AB", "R2")):
            acct = account()
            acct.account_ref = ref
            self.assertEqual("ADDED", s.add_account(ctx(person=person, ref=ref), acct, PROV, False)[0])
        self.assertEqual(3, len(s.snapshots))

    def test_unexpected_columns_are_refused_not_misread(self):
        self.dir.mkdir()
        (self.dir / "AB_snapshots.csv").write_text("old,layout\n1,2\n")
        with self.assertRaises(StoreError):
            self.store()


class GapTests(StoreTestCase):
    def test_missing_months_between_first_and_last(self):
        s = self.store()
        for m in ("2026-01-31", "2026-02-28", "2026-05-31"):
            s.add_account(ctx(), account(as_of=m), PROV, False)
        self.assertEqual({("prov", "AB", "R1"): ["2026-03", "2026-04"]}, find_gaps(s.snapshots.values()))

    def test_uncovered_stretches_between_ranges(self):
        s = self.store()
        s.add_account(ctx(source_type="activity_statement"),
                      account(totals=[PeriodTotal("deposits", D(1), "GBP")],
                              start="2025-04-05T21:00:00Z", end="2026-04-05T20:59:00Z"), PROV, False)
        s.add_account(ctx(source_type="activity_statement"),
                      account(as_of="2026-10-05T20:59:00Z", totals=[PeriodTotal("deposits", D(1), "GBP")],
                              start="2026-04-05T21:00:00Z", end="2026-10-05T20:59:00Z"), PROV, False)
        self.assertEqual({}, find_range_gaps(s.totals.values()))     # contiguous: one minute apart
        s.add_account(ctx(source_type="activity_statement"),
                      account(as_of="2027-04-05T20:59:00Z", totals=[PeriodTotal("deposits", D(1), "GBP")],
                              start="2027-01-01T00:00:00Z", end="2027-04-05T20:59:00Z"), PROV, False)
        self.assertEqual({("prov", "AB", "R1"): ["2026-10-05 to 2027-01-01"]},
                         find_range_gaps(s.totals.values()))


class IsolationTests(StoreTestCase):
    """Adding one provider's data must never alter another provider's stored rows."""

    def ingest(self, s, provider, ref):
        for name, path in fixture_cases(provider):
            if name.startswith(("bad_", "notes_")) or path.name.startswith(("bad_", "notes_")):
                continue
            doc = PROVIDERS[provider].parse_text(path.read_text())
            for a in doc.accounts:
                c = ctx(provider=provider, ref=a.account_ref or ref, source_type=doc.source_type,
                        account_type=a.account_type)
                s.add_account(c, a, {**PROV, "source_file": path.name}, True)

    def test_adding_a_provider_leaves_existing_rows_untouched(self):
        s = self.store()
        self.ingest(s, "freetrade", "ACC-123")
        s.save()
        before = self.store()
        free_before = {k: v for k, v in before.snapshots.items() if v["provider"] == "freetrade"}
        pos_before = {k: v for k, v in before.positions.items() if k[0] == "freetrade"}
        self.assertTrue(free_before)

        s2 = self.store()
        self.ingest(s2, "trading212", "11112222")
        s2.save()
        after = self.store()
        self.assertGreater(len(after.snapshots), len(free_before))
        self.assertEqual(free_before, {k: v for k, v in after.snapshots.items() if v["provider"] == "freetrade"})
        self.assertEqual(pos_before, {k: v for k, v in after.positions.items() if k[0] == "freetrade"})

    def test_ingest_order_of_providers_does_not_matter(self):
        a, b = Path(self._tmp.name) / "a", Path(self._tmp.name) / "b"
        sa, sb = Store(a, "AB"), Store(b, "AB")
        self.ingest(sa, "freetrade", "ACC-123"); self.ingest(sa, "trading212", "1")
        self.ingest(sb, "trading212", "1"); self.ingest(sb, "freetrade", "ACC-123")
        sa.save(); sb.save()
        for f in sorted(a.glob("AB_*.csv")):
            self.assertEqual(f.read_bytes(), (b / f.name).read_bytes(), f.name)


if __name__ == "__main__":
    unittest.main()
