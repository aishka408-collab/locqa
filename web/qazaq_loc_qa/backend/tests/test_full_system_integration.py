"""
test_full_system_integration.py
Full system integration test suite verifying the interaction between
FastAPI Endpoints, ARB Parser, all 11 Analyzers, Fix Engine, Glossary, and Exporters.
Uses direct async function execution to avoid ASGI in-memory deadlocks.
"""
import sys
import os
import asyncio
import hashlib
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.api.main import (
    analyze,
    submit_fixes,
    apply_fixes,
    undo_last,
    get_stats,
    get_report,
    get_errors,
    export_markdown,
    export_csv,
    add_glossary_term,
    get_glossary,
    delete_glossary_term,
    serve_ui,
    AnalyzeRequest,
    FixAction,
    GlossaryTerm,
    UPLOAD_DIR,
)
from qazaq_loc_qa.backend.utils.arb_parser import ARBFile


class TestFullSystemIntegration:

    def setup_method(self):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.ru_fid = "test_sys_intl_ru.arb"
        self.kk_fid = "test_sys_intl_kk.arb"
        self.ru_path = UPLOAD_DIR / self.ru_fid
        self.kk_path = UPLOAD_DIR / self.kk_fid

        ru_content = (
            '{\n'
            '    "welcome": "Добро пожаловать, {name}!",\n'
            '    "farewell": "До свидания",\n'
            '    "same": "Start",\n'
            '    "coins": "У вас {count} монет"\n'
            '}\n'
        )
        kk_content = (
            '{\n'
            '    "welcome": "Қош келдіңіз, {name}!",\n'
            '    "farewell": "Сау бол",\n'
            '    "same": "start",\n'
            '    "coins": "Сізде тиын бар"\n'
            '}\n'
        )
        self.ru_path.write_text(ru_content, encoding="utf-8")
        self.kk_path.write_text(kk_content, encoding="utf-8")

    def teardown_method(self):
        fixed_kk = UPLOAD_DIR / f"fixed_{self.kk_fid}"
        fixed_ru = UPLOAD_DIR / f"fixed_{self.ru_fid}"
        for p in [self.ru_path, self.kk_path, fixed_kk, fixed_ru]:
            if p.is_file():
                p.unlink()

    def test_full_pipeline_analyze_fix_undo_export(self):
        """End-to-end pipeline test."""
        # 1. Analyze
        req = AnalyzeRequest(
            file_ids=[self.ru_fid, self.kk_fid],
            source_file_id=self.ru_fid,
            target_file_id=self.kk_fid
        )
        an_resp = asyncio.run(analyze(req))
        aid = an_resp["analysis_id"]
        errors = an_resp["errors"]
        assert len(errors) > 0

        # Check standardized fields
        first = errors[0]
        assert "key" in first
        assert "category" in first
        assert "source" in first
        assert "current" in first

        # 2. Get filtered errors
        same_errs = asyncio.run(get_errors(aid, category="same_as_source"))
        assert all(e["category"] == "same_as_source" for e in same_errs)

        # 3. Submit fixes: accept farewell, reject same, manual coins
        fixes = [
            FixAction(key="farewell", action="accept", new_value="Сау болыңыздар"),
            FixAction(key="same", action="reject", new_value=None),
            FixAction(key="coins", action="manual", new_value="Сізде {count} монета бар")
        ]
        fix_resp = asyncio.run(submit_fixes(aid, fixes))
        assert fix_resp["count"] == 3

        # 4. Check stats
        stats = asyncio.run(get_stats(aid))
        assert stats.accepted_fixes == 2
        assert stats.rejected_fixes == 1

        # 5. Apply fixes
        apply_resp = asyncio.run(apply_fixes(aid))
        applied_files = apply_resp["applied_files"]
        assert self.kk_fid in applied_files

        fixed_path = Path(applied_files[self.kk_fid])
        assert fixed_path.is_file()
        fixed_arb = ARBFile.load(fixed_path)
        assert fixed_arb.get("farewell") == "Сау болыңыздар"
        assert fixed_arb.get("coins") == "Сізде {count} монета бар"

        # 6. Verify original uploaded file is untouched
        orig_arb = ARBFile.load(self.kk_path)
        assert orig_arb.get("farewell") == "Сау бол"

        # 7. Undo
        undo_resp = asyncio.run(undo_last(aid))
        assert "restored" in undo_resp
        restored_arb = ARBFile.load(fixed_path)
        assert restored_arb.get("farewell") == "Сау бол"

        # 8. Export Markdown
        md_resp = asyncio.run(export_markdown(aid))
        assert "# Qazaq Loc QA Report" in md_resp.body.decode("utf-8")

        # 9. Export CSV
        csv_resp = asyncio.run(export_csv(aid))
        assert "Key,Category,Confidence" in csv_resp.body.decode("utf-8")

    def test_glossary_crud_pipeline(self):
        """Glossary endpoint integration."""
        # Add term
        term = GlossaryTerm(ru_term="интеграция_тест", kk="интеграция_сынақ", variants=["сынақ"])
        add_resp = asyncio.run(add_glossary_term(term))
        assert add_resp["message"] == "Term added"

        # Get glossary
        gl = asyncio.run(get_glossary())
        assert "интеграция_тест" in gl
        assert gl["интеграция_тест"]["kk"] == "интеграция_сынақ"

        # Delete term
        del_resp = asyncio.run(delete_glossary_term("интеграция_тест"))
        assert del_resp["message"] == "Term deleted"

        # Confirm deleted
        gl2 = asyncio.run(get_glossary())
        assert "интеграция_тест" not in gl2

    def test_ui_served(self):
        """UI HTML serving."""
        resp = asyncio.run(serve_ui())
        content = resp.body.decode("utf-8")
        assert "Qazaq Loc QA" in content
        assert "Анализ локализации" in content
