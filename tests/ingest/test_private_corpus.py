"""Regression tests against your REAL statements. Skipped unless configured.

    INGEST_CORPUS  directory with one sub-directory per provider holding real PDFs:
                           $INGEST_CORPUS/freetrade/*.pdf
                           $INGEST_CORPUS/trading212/*.pdf
                       (symlinks are fine)
    INGEST_GOLDEN  directory OUTSIDE the repo for blessed outputs. These contain your
                       real figures, so never put them in the repo (and don't commit hashes
                       of them either: small statements are easy to brute-force).

First run, or after an intentional parser change you have reviewed:
    INGEST_UPDATE_GOLDEN=1 python -m unittest tests.ingest.test_private_corpus
Normal run:
    python -m unittest tests.ingest.test_private_corpus

Any difference from the blessed output fails. Because this uses the same canonical view as
the synthetic tests, it keeps working when the CSV layout changes.
"""
import difflib
import os
import unittest
from pathlib import Path

from statements_ingest.providers import PROVIDERS

from .support import canonical, dump, update_golden_requested

REPO_ROOT = Path(__file__).resolve().parents[2]


class PrivateCorpusTests(unittest.TestCase):
    def test_real_statements_parse_as_blessed(self):
        corpus, golden = os.environ.get("INGEST_CORPUS"), os.environ.get("INGEST_GOLDEN")
        if not corpus or not golden:
            self.skipTest("set INGEST_CORPUS and INGEST_GOLDEN to run against real statements")
        try:
            import pdfplumber  # noqa: F401
        except ImportError:
            self.skipTest("pdfplumber not installed")
        golden = Path(golden).expanduser().resolve()
        self.assertNotIn(REPO_ROOT, [golden, *golden.parents],
                         "INGEST_GOLDEN is inside the repo; it holds real figures, so keep it outside")

        checked = 0
        for provider, cls in PROVIDERS.items():
            pdir = Path(corpus).expanduser() / provider
            if not pdir.is_dir():
                continue
            parser = cls()
            for pdf in sorted(pdir.glob("*.pdf")):
                with self.subTest(provider=provider, pdf=pdf.name):
                    actual = dump(canonical(parser.read(pdf)))
                    target = golden / provider / f"{pdf.stem}.json"
                    if update_golden_requested():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(actual)
                        continue
                    self.assertTrue(target.exists(),
                                    f"no blessed output for {pdf.name}: run with INGEST_UPDATE_GOLDEN=1 and review it")
                    expected = target.read_text()
                    if expected != actual:
                        diff = "".join(difflib.unified_diff(
                            expected.splitlines(True), actual.splitlines(True), "blessed", "now", n=2))
                        self.fail(f"{pdf.name} no longer parses as blessed:\n{diff}")
                    checked += 1
        if not update_golden_requested():
            self.assertGreater(checked, 0, "corpus configured but no PDFs found under provider sub-directories")


if __name__ == "__main__":
    unittest.main()
