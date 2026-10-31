"""
test_glossary_persistence.py
Tests for glossary persistent storage and integration with glossary_violation analyzer.
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.glossary.service import GlossaryService
from qazaq_loc_qa.backend.utils.arb_parser import ARBFile
from qazaq_loc_qa.backend.analyzers.kazakh_analyzers import analyzer_glossary_violation


class TestGlossaryPersistence:

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.glossary_file = self.tmpdir / "glossary.json"
        self.service = GlossaryService(self.glossary_file)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_crud_and_persistence(self):
        """Test adding, updating, getting, and deleting terms with file persistence."""
        # 1. Add terms
        self.service.add_term("бонус", "бонус", ["бонус", "бонусы"])
        self.service.add_term("монета", "монета", ["монеталар"])

        assert self.glossary_file.is_file(), "Glossary file must be created on disk"
        terms = self.service.get_all_terms()
        assert "бонус" in terms
        assert "монета" in terms
        assert terms["монета"]["kk"] == "монета"

        # 2. Simulate server restart: create a new service pointing to same file
        restarted_service = GlossaryService(self.glossary_file)
        restarted_terms = restarted_service.get_all_terms()
        assert "бонус" in restarted_terms
        assert restarted_terms["бонус"]["variants"] == ["бонус", "бонусы"]

        # 3. Update term
        restarted_service.update_term("бонус", "сыйақы", ["сыйақы"])
        assert restarted_service.get_term("бонус")["kk"] == "сыйақы"

        # Verify persisted update
        third_service = GlossaryService(self.glossary_file)
        assert third_service.get_term("бонус")["kk"] == "сыйақы"

        # 4. Delete term
        third_service.delete_term("монета")
        assert third_service.get_term("монета") is None
        assert "монета" not in third_service.get_all_terms()

        # Check persistence of deletion
        fourth_service = GlossaryService(self.glossary_file)
        assert "монета" not in fourth_service.get_all_terms()

    def test_analyzer_uses_fresh_glossary(self):
        """Ensure analyzer_glossary_violation uses the updated terms, not stale copy."""
        import qazaq_loc_qa.backend.glossary.service as gs_module
        old_default = gs_module.glossary_service
        try:
            # Point global glossary_service to our test file
            gs_module.glossary_service = self.service

            # Create sample ARBs
            ru_path = self.tmpdir / "ru.arb"
            kk_path = self.tmpdir / "kk.arb"

            ru_path.write_text('{\n    "btn_pay": "Оплатить бонус"\n}\n', encoding="utf-8")
            kk_path.write_text('{\n    "btn_pay": "Төлеу қате"\n}\n', encoding="utf-8")

            ru = ARBFile.load(ru_path)
            kk = ARBFile.load(kk_path)

            # Initially, empty glossary -> 0 violations
            violations = analyzer_glossary_violation(ru, kk)
            assert len(violations) == 0

            # Add term to glossary
            self.service.add_term("бонус", "сыйақы", ["сыйақылар"])

            # Re-run analyzer: must immediately pick up newly added term without restart
            violations2 = analyzer_glossary_violation(ru, kk)
            assert len(violations2) == 1
            assert violations2[0]["key"] == "btn_pay"
            assert violations2[0]["suggestion"] == "сыйақы"

            # Now translate it properly
            kk.set("btn_pay", "Төлеу сыйақы")
            violations3 = analyzer_glossary_violation(ru, kk)
            assert len(violations3) == 0

        finally:
            gs_module.glossary_service = old_default
