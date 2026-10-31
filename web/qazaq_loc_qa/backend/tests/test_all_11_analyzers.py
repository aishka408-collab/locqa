"""
test_all_11_analyzers.py
Verifies that all 11 error analyzers work individually and through the unified orchestrator.
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.utils.arb_parser import ARBFile
from qazaq_loc_qa.backend.analysis.orchestrator import run_all_analyzers, ANALYZERS
from qazaq_loc_qa.backend.glossary.service import GlossaryService
import qazaq_loc_qa.backend.glossary.service as gs_module


class TestAll11Analyzers:

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.glossary_file = self.tmpdir / "glossary.json"
        self.glossary_service = GlossaryService(self.glossary_file)
        self.old_service = gs_module.glossary_service
        gs_module.glossary_service = self.glossary_service

    def teardown_method(self):
        gs_module.glossary_service = self.old_service
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_arbs(self, ru_dict, kk_dict):
        ru_p = self.tmpdir / "intl_ru.arb"
        kk_p = self.tmpdir / "intl_kk.arb"

        import json
        ru_p.write_text(json.dumps(ru_dict, ensure_ascii=False, indent=2), encoding="utf-8")
        kk_p.write_text(json.dumps(kk_dict, ensure_ascii=False, indent=2), encoding="utf-8")

        return ARBFile.load(ru_p), ARBFile.load(kk_p)

    def test_registered_analyzers_count(self):
        """Must have exactly 11 registered analyzers."""
        names = [name for name, _ in ANALYZERS]
        expected = [
            "untranslated",
            "same_as_source",
            "placeholder_mismatch",
            "wrong_language",
            "length_overflow",
            "suffix_harmony",
            "orthography",
            "plural_mismatch",
            "glossary_violation",
            "inconsistent_term",
            "machine_translation_suspect",
        ]
        assert len(names) == 11
        for exp in expected:
            assert exp in names, f"Missing analyzer: {exp}"

    def test_all_11_categories_detected_via_orchestrator(self):
        """Provide a composite dataset designed to trigger all 11 categories."""
        # Setup glossary term for glossary_violation
        self.glossary_service.add_term("бонус", "сыйақы", ["сыйақылар"])

        ru_data = {
            "key_untranslated": "Привет мир",
            "key_same_as_source": "START",
            "key_placeholder_mismatch": "У вас {count} монет",
            "key_wrong_language": "Привет друг",
            "key_length_overflow": "Да, я согласен",
            "key_suffix_harmony": "Город",
            "key_orthography": "Текст",
            "key_plural_mismatch": "Всего {count} предметов",
            "key_glossary_violation": "Оплатить бонус",
            "key_inconsistent_1": "Покупка",
            "key_inconsistent_2": "Покупка",
            "key_machine_translation": "Настройки",
        }

        kk_data = {
            "key_untranslated": "Привет мир",               # untranslated
            "key_same_as_source": "start",                  # same_as_source
            "key_placeholder_mismatch": "Сізде тиын бар",   # placeholder_mismatch (missing {count})
            "key_wrong_language": "Добрый день",            # wrong_language (Russian ending -ый)
            "key_length_overflow": "Иә, әрине, әбден келісемін", # length_overflow
            "key_suffix_harmony": "Қызметта",               # suffix_harmony (қызмет + та, front stem)
            "key_orthography": "Қазақстан Tex",             # orthography (unexpected Latin token)
            "key_plural_mismatch": "Барлығы {count} зат",   # plural_mismatch (no лар/лер)
            "key_glossary_violation": "Төлеу қате",         # glossary_violation (missing 'сыйақы')
            "key_inconsistent_1": "Көлік сатып алу",        # inconsistent_term (same source, 2 translations)
            "key_inconsistent_2": "Үй алу",
            "key_machine_translation": "Бұл әр для баптау",  # machine_translation_suspect (contains 'для')
        }

        ru, kk = self._create_arbs(ru_data, kk_data)
        findings = run_all_analyzers(ru, kk)

        detected_categories = {f["category"] for f in findings}

        # Check all 11 categories
        for cat in [
            "untranslated",
            "same_as_source",
            "placeholder_mismatch",
            "wrong_language",
            "length_overflow",
            "suffix_harmony",
            "orthography",
            "plural_mismatch",
            "glossary_violation",
            "inconsistent_term",
            "machine_translation_suspect",
        ]:
            assert cat in detected_categories, f"Category '{cat}' was not detected in orchestrator run!"

    def test_analyzer_exception_isolation(self):
        """A broken analyzer must not stop the remaining analyzers."""
        from unittest.mock import patch

        def failing_analyzer(ru, kk):
            raise ValueError("Intentional simulated crash in analyzer")

        # Temporarily insert failing analyzer at the front
        test_analyzers = [("broken", failing_analyzer)] + ANALYZERS
        with patch("qazaq_loc_qa.backend.analysis.orchestrator.ANALYZERS", test_analyzers):
            ru, kk = self._create_arbs({"test": "Привет"}, {"test": "Привет"})
            findings = run_all_analyzers(ru, kk)
            # untranslated should still be found despite "broken" crashing
            categories = [f["category"] for f in findings]
            assert "untranslated" in categories
