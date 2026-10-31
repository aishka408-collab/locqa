"""
test_analyzer_refinements.py
Regression tests for refined analyzer heuristics:
- orthography: Latin/brand strings without Cyrillic source are not flagged.
- untranslated: Latin sources (brands/names) with equal translation are not flagged.
"""
import sys
import os
import tempfile
import json
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.utils.arb_parser import ARBFile
from qazaq_loc_qa.backend.analysis.orchestrator import run_all_analyzers


class TestAnalyzerRefinements:

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ru_path = self.tmpdir / "intl_ru.arb"
        self.kk_path = self.tmpdir / "intl_kk.arb"

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_arbs(self, ru_dict, kk_dict):
        self.ru_path.write_text(json.dumps(ru_dict, ensure_ascii=False, indent=2), encoding="utf-8")
        self.kk_path.write_text(json.dumps(kk_dict, ensure_ascii=False, indent=2), encoding="utf-8")
        return ARBFile.load(self.ru_path), ARBFile.load(self.kk_path)

    def test_orthography_skips_latin_source(self):
        """Latin-source strings (lang codes, brands) with Latin translation are not orthography errors."""
        ru, kk = self._write_arbs({"lang": "en", "currency": "USD", "brand_name": "Grand"}, {"lang": "kk", "currency": "USD", "brand_name": "Grand"})
        errors = run_all_analyzers(ru, kk)
        cats = {e["category"] for e in errors}
        assert "orthography" not in cats, f"False positive orthography for Latin-source keys: {errors}"

    def test_orthography_still_flags_cyrillic_source_with_latin_target(self):
        """Cyrillic source + Latin target must still be flagged (regression guard)."""
        ru, kk = self._write_arbs({"proper_term": "Текст"}, {"proper_term": "Tex"})
        errors = run_all_analyzers(ru, kk)
        cats = [e["category"] for e in errors]
        assert "orthography" in cats, "Cyrillic source with Latin target should be flagged"

    def test_untranslated_skips_latin_source_equal_translation(self):
        """Latin source with identical translation is a legitimate brand/name, not untranslated."""
        ru, kk = self._write_arbs({"brand_name": "Grand", "game_name": "Grand"}, {"brand_name": "Grand", "game_name": "Grand"})
        errors = run_all_analyzers(ru, kk)
        cats = [e["category"] for e in errors]
        assert "untranslated" not in cats, "Latin-source equal translation should not be untranslated"

    def test_untranslated_still_flags_cyrillic_source_equal_translation(self):
        """Cyrillic source with identical translation must still be flagged (regression guard)."""
        ru, kk = self._write_arbs({"welcome": "Привет мир"}, {"welcome": "Привет мир"})
        errors = run_all_analyzers(ru, kk)
        cats = [e["category"] for e in errors]
        assert "untranslated" in cats, "Cyrillic source with equal translation should be flagged"
