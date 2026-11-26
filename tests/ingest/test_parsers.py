"""Adapter tests: pure text in, canonical documents out. No PDFs, no CSVs, no store.

Fixture naming convention (tests/fixtures/<provider>/NAME.txt):
    bad_*     must produce hard problems (the statement would be rejected)
    notes_*   must produce no hard problems but at least one soft check note
    limitation_*  documents a known gap: parses cleanly today but not correctly (see the
              fixture's expected output); when the gap is fixed the expected file changes
    anything else must be completely clean

Regenerate expected files after an intentional change:
    INGEST_UPDATE_GOLDEN=1 python -m unittest tests.ingest.test_parsers
then review the diff in git before committing.
"""
import re
import unittest

from statements_ingest.models import ACCOUNT_TYPES, TRANSACTION_KINDS, validate_account
from statements_ingest.providers import PROVIDERS

from .support import FIXTURES, canonical, dump, fixture_cases, update_golden_requested

ISO_AS_OF = re.compile(r"^\d{4}-\d\d-\d\d(T\d\d:\d\d:\d\dZ)?$")
ISO_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
CCY = re.compile(r"^[A-Z]{3}$")


def parse(provider: str, text: str):
    return PROVIDERS[provider].parse_text(text)


def hard_problems(doc):
    problems = list(doc.warnings)
    for a in doc.accounts:
        problems += a.warnings + validate_account(a)
    if not doc.accounts:
        problems.append("no accounts")
    return problems


class GoldenTests(unittest.TestCase):
    def test_fixtures_match_expected_output(self):
        cases = fixture_cases()
        self.assertTrue(cases, "no fixtures found")
        for provider, path in cases:
            with self.subTest(provider=provider, fixture=path.name):
                actual = dump(canonical(parse(provider, path.read_text())))
                expected = path.with_suffix(".expected.json")
                if update_golden_requested():
                    expected.write_text(actual)
                    continue
                self.assertTrue(expected.exists(),
                                f"missing {expected.name}: run with INGEST_UPDATE_GOLDEN=1 and review it")
                self.assertEqual(expected.read_text(), actual)


class ContractTests(unittest.TestCase):
    """Rules every adapter must obey. A new provider is covered by adding fixtures."""

    def test_registry_and_fixtures_agree(self):
        for name, cls in PROVIDERS.items():
            with self.subTest(provider=name):
                self.assertEqual(name, cls.name)
                self.assertIn(cls.coverage, ("monthly", "ranges"))
                self.assertTrue(cls.version)
                self.assertTrue(callable(getattr(cls, "parse_text", None)), "adapters must expose parse_text")
                self.assertTrue(hasattr(cls.discoverer, "discover"))
                clean = [p for _, p in fixture_cases(name)
                         if not p.name.startswith(("bad_", "notes_"))]
                self.assertTrue(clean, f"{name} has no clean fixture in tests/fixtures/{name}/")
        for pdir in FIXTURES.iterdir():
            if pdir.is_dir():
                self.assertIn(pdir.name, PROVIDERS, f"tests/ingest/fixtures/{pdir.name} has no registered ingest provider")

    def test_fixture_name_conventions(self):
        for provider, path in fixture_cases():
            with self.subTest(provider=provider, fixture=path.name):
                doc = parse(provider, path.read_text())
                problems = hard_problems(doc)
                notes = [n for a in doc.accounts for n in a.notes]
                if path.name.startswith("bad_"):
                    self.assertTrue(problems, "expected hard problems")
                elif path.name.startswith("notes_"):
                    self.assertFalse(problems, f"unexpected hard problems: {problems}")
                    self.assertTrue(notes, "expected at least one soft check note")
                else:
                    self.assertFalse(problems, f"unexpected hard problems: {problems}")
                    self.assertFalse(notes, f"unexpected check notes: {notes}")

    def test_invariants_on_accepted_documents(self):
        for provider, path in fixture_cases():
            doc = parse(provider, path.read_text())
            if hard_problems(doc):
                continue
            with self.subTest(provider=provider, fixture=path.name):
                self.assertTrue(doc.source_type)
                for a in doc.accounts:
                    self.assertIn(a.account_type, ACCOUNT_TYPES)
                    self.assertTrue(a.account_ref or provider == "freetrade", "account_ref missing")
                    self.assertRegex(a.as_of, ISO_AS_OF)
                    self.assertRegex(a.currency, CCY)
                    if a.period_start or a.period_end:
                        self.assertRegex(a.period_start, ISO_TS)
                        self.assertRegex(a.period_end, ISO_TS)
                        self.assertLessEqual(a.period_start, a.period_end)
                    for p in a.positions:
                        self.assertRegex(p.currency, CCY)
                        self.assertTrue(p.instrument_name)
                    for t in a.totals:
                        self.assertRegex(t.currency, CCY)
                        self.assertRegex(t.measure, r"^[a-z0-9_]+$")
                    for t in a.transactions:
                        self.assertIn(t.kind, TRANSACTION_KINDS)
                        self.assertRegex(t.timestamp, ISO_TS)
                        if t.kind in ("buy", "sell"):
                            self.assertIsNotNone(t.amount)
                            self.assertEqual(t.quantity < 0, t.kind == "sell")

    def test_parsing_is_pure_and_layout_insensitive(self):
        """Same text twice, or the same text re-indented with extra blank lines, gives the same result."""
        for provider, path in fixture_cases():
            with self.subTest(provider=provider, fixture=path.name):
                text = path.read_text()
                baseline = canonical(parse(provider, text))
                self.assertEqual(baseline, canonical(parse(provider, text)))
                reflowed = "".join("    " + ln + "\n\n" for ln in text.splitlines())
                self.assertEqual(baseline, canonical(parse(provider, reflowed)))


if __name__ == "__main__":
    unittest.main()
