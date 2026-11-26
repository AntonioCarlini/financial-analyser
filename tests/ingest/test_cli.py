"""End-to-end tests through main(), using synthetic text standing in for PDFs.

Each fake 'PDF' is a text file; extract_text is patched to read it. Everything after
text extraction (discovery, parsing, validation, cross-checks, store, reporting) is real.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from statements_ingest.__main__ import main
from statements_ingest.providers.freetrade import FreetradeParser
from statements_ingest.providers.trading212 import Trading212Parser

from .support import FIXTURES

read_text = staticmethod(lambda p: Path(p).read_text())


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.inbox, self.store = self.root / "inbox", self.root / "store"
        self.inbox.mkdir()
        for cls in (FreetradeParser, Trading212Parser):
            p = mock.patch.object(cls, "extract_text", read_text)
            p.start()
            self.addCleanup(p.stop)

    def put(self, name, fixture):
        provider, fname = fixture.split("/")
        (self.inbox / name).write_text((FIXTURES / provider / fname).read_text())

    def run_cli(self, *extra, provider="freetrade"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main([str(self.inbox), "--store", str(self.store), "--person", "ab",
                       "--provider", provider, *extra])
        return rc, out.getvalue()

    def snapshot_files(self):
        return {p.name: p.read_bytes() for p in sorted(self.store.glob("*.csv"))} if self.store.exists() else {}


class FreetradeCliTests(CliTestCase):
    def test_ingest_then_rerun_changes_nothing(self):
        self.put("GIA-Statement-2026-08.pdf", "freetrade/gia_with_holdings.txt")
        self.put("GIA-Statement-2026-10.pdf", "freetrade/gia_cash_only.txt")
        rc, out = self.run_cli()
        self.assertEqual(0, rc, out)
        self.assertEqual(2, out.count("ADDED"), out)
        self.assertIn("Missing statements", out)                  # September is absent
        first = self.snapshot_files()
        self.assertEqual({"AB_snapshots.csv", "AB_positions.csv", "AB_period_totals.csv",
                          "AB_transactions.csv"}, set(first))
        rc, out = self.run_cli()
        self.assertEqual(0, rc)
        self.assertEqual(2, out.count("UNCHANGED"), out)
        self.assertEqual(first, self.snapshot_files())

    def test_positions_values_and_order_in_store(self):
        self.put("GIA-Statement-2026-08.pdf", "freetrade/gia_with_holdings.txt")
        self.run_cli()
        rows = (self.store / "AB_positions.csv").read_text().splitlines()[1:]
        self.assertEqual(3, len(rows))
        self.assertTrue(any("12.5" in r and "2500.00" in r for r in rows), rows)   # fractional quantity kept

    def test_filename_month_must_agree_with_statement_title(self):
        self.put("GIA-Statement-2026-09.pdf", "freetrade/gia_with_holdings.txt")   # contents say August
        rc, out = self.run_cli()
        self.assertEqual(2, rc)
        self.assertIn("filename says 2026-09 but statement says 2026-08", out)
        self.assertEqual({}, self.snapshot_files())                                 # nothing created

    def test_missing_account_reference_needs_a_fallback_and_is_checked(self):
        self.put("GIA-Statement-2024-02.pdf", "freetrade/gia_no_account_reference.txt")
        rc, out = self.run_cli()
        self.assertEqual(2, rc)
        self.assertIn("--account-ref", out)
        rc, out = self.run_cli("--account-ref", "ACC-123")
        self.assertEqual(0, rc, out)
        self.put("GIA-Statement-2026-08.pdf", "freetrade/gia_with_holdings.txt")
        rc, out = self.run_cli("--account-ref", "SOMETHING-ELSE")
        self.assertEqual(2, rc)
        self.assertIn("differs from --account-ref", out)

    def test_every_bad_fixture_is_rejected_and_nothing_is_stored(self):
        for fixture in sorted((FIXTURES / "freetrade").glob("bad_*.txt")):
            with self.subTest(fixture=fixture.name):
                for f in self.inbox.iterdir():
                    f.unlink()
                self.put("GIA-Statement-2026-08.pdf", f"freetrade/{fixture.name}")
                rc, out = self.run_cli()
                self.assertEqual(2, rc, out)
                self.assertIn("REJECTED", out)
                self.assertEqual({}, self.snapshot_files())

    def test_dry_run_writes_nothing(self):
        self.put("GIA-Statement-2026-08.pdf", "freetrade/gia_with_holdings.txt")
        rc, out = self.run_cli("--dry-run")
        self.assertEqual(0, rc)
        self.assertIn("Dry run", out)
        self.assertEqual({}, self.snapshot_files())

    def test_files_that_do_not_match_the_naming_scheme_are_listed_not_parsed(self):
        self.put("GIA-Statement-2026-08.pdf", "freetrade/gia_with_holdings.txt")
        (self.inbox / "random-notes.pdf").write_text("hello")
        rc, out = self.run_cli()
        self.assertEqual(0, rc, out)
        self.assertIn("IGNORED   random-notes.pdf", out)


class Trading212CliTests(CliTestCase):
    def test_two_account_statement_ingests_both_and_rerun_is_unchanged(self):
        self.put("Activity-Statement-2025-04-06-2026-04-05.pdf", "trading212/activity_two_accounts.txt")
        rc, out = self.run_cli(provider="trading212")
        self.assertEqual(0, rc, out)
        self.assertIn("[ISA]", out)
        self.assertIn("[GIA]", out)
        first = self.snapshot_files()
        rc, out = self.run_cli(provider="trading212")
        self.assertEqual(2, out.count("UNCHANGED"), out)
        self.assertEqual(first, self.snapshot_files())

    def test_soft_check_failures_are_reported_but_data_is_stored(self):
        self.put("Activity-Statement-x.pdf", "trading212/notes_overview_does_not_reconcile.txt")
        rc, out = self.run_cli(provider="trading212")
        self.assertEqual(0, rc, out)
        self.assertIn("check:", out)
        self.assertTrue((self.store / "AB_snapshots.csv").exists())

    def test_unknown_transaction_type_rejects_that_account_by_name(self):
        self.put("Activity-Statement-x.pdf", "trading212/bad_unknown_transaction_type.txt")
        rc, out = self.run_cli(provider="trading212")
        self.assertEqual(2, rc)
        self.assertIn("Mystery Transfer", out)


if __name__ == "__main__":
    unittest.main()
