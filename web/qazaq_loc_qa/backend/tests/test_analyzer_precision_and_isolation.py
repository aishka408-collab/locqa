"""Precision, regression and isolation tests for the QA improvements.

These tests encode the behaviour that was fixed on real ARB data:

* the old ``wrong_language`` / ``inconsistent_term`` heuristics produced
  thousands of false positives;
* ARB JSON escapes were not decoded before analysis;
* fixes were applied to every uploaded file instead of only the target.

The tests are intentionally small and deterministic.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.utils.arb_parser import ARBFile
from qazaq_loc_qa.backend.analyzers.base_analyzers import (
    analyzer_length_overflow,
    analyzer_wrong_language,
)
from qazaq_loc_qa.backend.analyzers.kazakh_analyzers import (
    analyzer_inconsistent_term,
    analyzer_machine_translation_suspect,
    analyzer_suffix_harmony,
)
from qazaq_loc_qa.backend.analyzers.text_utils import normalize_for_compare
from qazaq_loc_qa.backend.api.main import (
    UPLOAD_DIR,
    AnalyzeRequest,
    FixAction,
    _persist,
    _restore_runtime,
    analyses_runtime,
    analyze,
    apply_fixes,
    export_csv,
    export_markdown,
    get_report,
    submit_fixes,
    undo_last,
)


def _write_arb(path: Path, mapping: dict) -> None:
    lines = ["{\n"]
    keys = list(mapping)
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        lines.append(f'    "{key}": {json.dumps(mapping[key], ensure_ascii=False)}{comma}\n')
    lines.append("}\n")
    path.write_text("".join(lines), encoding="utf-8")


def _pair(tmp_path: Path, ru: dict, target: dict):
    ru_path = tmp_path / "intl_ru.arb"
    tr_path = tmp_path / "intl_kk.arb"
    _write_arb(ru_path, ru)
    _write_arb(tr_path, target)
    return ARBFile.load(ru_path), ARBFile.load(tr_path)


class TestAnalyzerPrecision:
    def test_wrong_language_does_not_flag_uppercase_kazakh_or_shared_letters(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"format_d": "ДН", "cancel": "Отмена", "ok": "Хорошо"},
            {"format_d": "КҮН", "cancel": "Болдырмау", "ok": "Жарайды"},
        )
        assert analyzer_wrong_language(ru, kk) == []

    def test_wrong_language_flags_real_russian_text(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"greeting": "Привет"},
            {"greeting": "Добрый день"},
        )
        findings = analyzer_wrong_language(ru, kk)
        assert [f["key"] for f in findings] == ["greeting"]
        assert findings[0]["confidence"] in {"medium", "high"}

    def test_wrong_language_flags_short_russian_stopword(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"profile": "Профи"},
            {"profile": "Про"},
        )
        findings = analyzer_wrong_language(ru, kk)
        assert [f["key"] for f in findings] == ["profile"]

    def test_wrong_language_does_not_flag_short_kazakh_number(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"ten": "Десять"},
            {"ten": "Он"},
        )
        assert analyzer_wrong_language(ru, kk) == []

    def test_wrong_language_flags_latin_other_language(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"auth": "Ошибка авторизации"},
            {"auth": "Avtorizatsiya xatosi"},
        )
        findings = analyzer_wrong_language(ru, kk)
        assert [f["key"] for f in findings] == ["auth"]

    def test_wrong_language_ignores_latin_brands(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"pass": "Сезонный пропуск"},
            {"pass": "Season Pass"},
        )
        assert analyzer_wrong_language(ru, kk) == []

    def test_wrong_language_detects_cyrillic_in_latin_target(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"jackpot": "Джекпот"},
            {"jackpot": "jекпот"},
        )
        # Force a Latin locale as if this were the Uzbek file.
        kk.entries.insert(0, type(kk.entries[0])("comment", "@@locale", "uz", '  "@@locale": "uz",\n'))
        findings = analyzer_wrong_language(ru, kk)
        assert [f["key"] for f in findings] == ["jackpot"]

    def test_inconsistent_term_reports_only_minority_variant(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"c1": "Отмена", "c2": "Отмена", "c3": "Отмена"},
            {"c1": "Болдырмау", "c2": "Болдырмау", "c3": "БАСТАУ"},
        )
        findings = analyzer_inconsistent_term(ru, kk)
        assert [f["key"] for f in findings] == ["c3"]
        assert findings[0]["confidence"] == "medium"

    def test_inconsistent_term_tie_is_marked_ambiguous(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"a": "Ранг", "b": "Ранг"},
            {"a": "Дәреже", "b": "Ранг"},
        )
        findings = analyzer_inconsistent_term(ru, kk)
        assert len(findings) == 2
        assert all(f["confidence"] == "low" for f in findings)
        assert all("Неоднозначно" in f["explanation"] for f in findings)

    def test_inconsistent_term_ignores_identical_translations(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"a": "Отмена", "b": "Отмена"},
            {"a": "Болдырмау", "b": "Болдырмау"},
        )
        assert analyzer_inconsistent_term(ru, kk) == []

    def test_length_overflow_ignores_short_strings(self, tmp_path):
        ru, kk = _pair(tmp_path, {"a": "Да"}, {"a": "Иә, әрине, әбден келісемін"})
        assert analyzer_length_overflow(ru, kk) == []

    def test_length_overflow_flags_real_growth(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"a": "Пополнить баланс или испытать удачу"},
            {"a": "Балансыңызды толықтырыңыз немесе ойында бағыңызды сынап көріңіз"},
        )
        findings = analyzer_length_overflow(ru, kk)
        assert [f["key"] for f in findings] == ["a"]

    def test_length_overflow_ignores_placeholders(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"a": "{name}, у вас {count} монет"},
            {"a": "{name}, сізде {count} монета бар"},
        )
        assert analyzer_length_overflow(ru, kk) == []

    def test_suffix_harmony_accepts_correct_and_glide_forms(self, tmp_path):
        ru, kk = _pair(
            tmp_path,
            {"a": "Город", "b": "Ждать", "c": "До"},
            {"a": "Балада", "b": "жеткізуге", "c": "Қызметте"},
        )
        assert analyzer_suffix_harmony(ru, kk) == []

    def test_suffix_harmony_flags_clear_violation(self, tmp_path):
        ru, kk = _pair(tmp_path, {"a": "Сервис"}, {"a": "Қызметта"})
        findings = analyzer_suffix_harmony(ru, kk)
        assert [f["key"] for f in findings] == ["a"]

    def test_machine_translation_flags_mixed_russian(self, tmp_path):
        ru, kk = _pair(tmp_path, {"a": "Настройки"}, {"a": "Бұл әр для баптау"})
        findings = analyzer_machine_translation_suspect(ru, kk)
        assert [f["key"] for f in findings] == ["a"]

    def test_machine_translation_ignores_clean_kazakh(self, tmp_path):
        ru, kk = _pair(tmp_path, {"a": "Настройки"}, {"a": "Бұл баптаулар"})
        assert analyzer_machine_translation_suspect(ru, kk) == []


class TestParserEscapes:
    def test_values_are_decoded_and_untouched_lines_preserved(self, tmp_path):
        path = tmp_path / "intl_kk.arb"
        path.write_text(
            '{\n'
            '  "@@locale": "kk",\n'
            '  "line": "Бірінші\\nекінші \\uea34",\n'
            '  "plain": "Қарапайым"\n'
            '}\n',
            encoding="utf-8",
        )
        arb = ARBFile.load(path)
        assert "\n" in arb.get("line")
        assert "\uea34" in arb.get("line")
        assert arb.locale == "kk"

        original_raw = next(e.raw for e in arb.entries if e.key == "line")
        arb.set("line", "Жаңа\nжол")
        out = tmp_path / "out.arb"
        arb.dump(out)
        text = out.read_text(encoding="utf-8")
        # Modified line is re-escaped, untouched line stays byte-identical.
        assert '"Жаңа\\nжол"' in text
        assert '"Қарапайым"' in text
        assert original_raw not in text  # it was the one modified line

        reloaded = ARBFile.load(out)
        assert reloaded.get("line") == "Жаңа\nжол"


class TestFixIsolationAndExports:
    def setup_method(self):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.ru_fid = "iso_test_intl_ru.arb"
        self.kk_fid = "iso_test_intl_kk.arb"
        self.ru_path = UPLOAD_DIR / self.ru_fid
        self.kk_path = UPLOAD_DIR / self.kk_fid
        _write_arb(self.ru_path, {"coins": "У вас {count} монет", "cancel": "Отмена"})
        _write_arb(self.kk_path, {"coins": "Сізде тиын бар", "cancel": "Болдырмау"})

    def teardown_method(self):
        for name in (
            self.ru_fid,
            self.kk_fid,
            f"fixed_{self.ru_fid}",
            f"fixed_{self.kk_fid}",
        ):
            p = UPLOAD_DIR / name
            if p.is_file():
                p.unlink()
        for aid, data in list(analyses_runtime.items()):
            if self.kk_fid in data.get("files", {}):
                analyses_runtime.pop(aid, None)

    def _analyze(self):
        req = AnalyzeRequest(
            file_ids=[self.ru_fid, self.kk_fid],
            source_file_id=self.ru_fid,
            target_file_id=self.kk_fid,
        )
        return asyncio.run(analyze(req))

    def test_fixes_are_applied_only_to_target(self):
        aid = self._analyze()["analysis_id"]
        asyncio.run(submit_fixes(aid, [
            FixAction(key="cancel", action="manual", new_value="Бас тарту"),
        ]))
        applied = asyncio.run(apply_fixes(aid))["applied_files"]
        assert list(applied) == [self.kk_fid]
        assert (UPLOAD_DIR / f"fixed_{self.kk_fid}").is_file()
        assert not (UPLOAD_DIR / f"fixed_{self.ru_fid}").exists()
        # Original source is untouched.
        assert ARBFile.load(self.ru_path).get("cancel") == "Отмена"

    def test_fix_updates_duplicate_keys_effectively(self):
        self.kk_path.write_text(
            '{\n'
            '    "cancel": "Болдырмау",\n'
            '    "cancel": "БАСТАУ"\n'
            '}\n',
            encoding="utf-8",
        )
        aid = self._analyze()["analysis_id"]
        asyncio.run(submit_fixes(aid, [
            FixAction(key="cancel", action="manual", new_value="Бас тарту"),
        ]))
        asyncio.run(apply_fixes(aid))
        fixed_path = UPLOAD_DIR / f"fixed_{self.kk_fid}"
        loaded = ARBFile.load(fixed_path)
        assert loaded.get("cancel") == "Бас тарту"
        occurrences = [e for e in loaded.entries if e.type == "kv" and e.key == "cancel"]
        assert [e.value for e in occurrences] == ["Бас тарту", "Бас тарту"]

    def test_report_and_csv_include_errors(self):
        aid = self._analyze()["analysis_id"]
        report = asyncio.run(get_report(aid))
        payload = json.loads(report.body)
        assert payload["error_count"] == len(payload["errors"])
        assert payload["error_count"] > 0
        csv_body = asyncio.run(export_csv(aid)).body.decode("utf-8")
        # One header + one row per error.
        assert len(csv_body.strip().splitlines()) == payload["error_count"] + 1
        md_body = asyncio.run(export_markdown(aid)).body.decode("utf-8")
        assert "coins" in md_body

    def test_undo_after_restart(self):
        aid = self._analyze()["analysis_id"]
        asyncio.run(submit_fixes(aid, [
            FixAction(key="cancel", action="manual", new_value="Бас тарту"),
        ]))
        asyncio.run(apply_fixes(aid))
        fixed_path = UPLOAD_DIR / f"fixed_{self.kk_fid}"
        assert ARBFile.load(fixed_path).get("cancel") == "Бас тарту"

        history_before = list(analyses_runtime[aid]["history"])
        assert history_before, "apply must record history"

        # Simulate a process restart: drop in-memory state and reload from disk.
        analyses_runtime.clear()
        _restore_runtime()
        assert aid in analyses_runtime
        assert len(analyses_runtime[aid]["history"]) == len(history_before)
        asyncio.run(undo_last(aid))
        assert ARBFile.load(fixed_path).get("cancel") == "Болдырмау"

    def test_undo_recreates_deleted_fixed_file_from_backup(self):
        aid = self._analyze()["analysis_id"]
        asyncio.run(submit_fixes(aid, [
            FixAction(key="cancel", action="manual", new_value="Бас тарту"),
        ]))
        asyncio.run(apply_fixes(aid))
        fixed_path = UPLOAD_DIR / f"fixed_{self.kk_fid}"
        fixed_path.unlink()
        analyses_runtime.clear()
        _restore_runtime()
        asyncio.run(undo_last(aid))
        assert fixed_path.is_file()
        assert ARBFile.load(fixed_path).get("cancel") == "Болдырмау"

    def test_placeholder_validation_rejects_bad_manual_fix(self):
        aid = self._analyze()["analysis_id"]
        with pytest.raises(HTTPException) as exc:
            asyncio.run(submit_fixes(aid, [
                FixAction(key="coins", action="manual", new_value="Сізде монета бар"),
            ]))
        assert exc.value.status_code in {400, 422}
        # Nothing has been recorded for the key.
        assert "coins" not in analyses_runtime[aid]["fixes"]

    def test_accept_fix_is_applied(self):
        aid = self._analyze()["analysis_id"]
        asyncio.run(submit_fixes(aid, [
            FixAction(key="cancel", action="accept", new_value="Бас тарту"),
        ]))
        asyncio.run(apply_fixes(aid))
        fixed_path = UPLOAD_DIR / f"fixed_{self.kk_fid}"
        assert ARBFile.load(fixed_path).get("cancel") == "Бас тарту"

    def test_rejected_fix_is_not_applied(self):
        aid = self._analyze()["analysis_id"]
        asyncio.run(submit_fixes(aid, [
            FixAction(key="cancel", action="reject", new_value=None),
        ]))
        asyncio.run(apply_fixes(aid))
        fixed_path = UPLOAD_DIR / f"fixed_{self.kk_fid}"
        assert ARBFile.load(fixed_path).get("cancel") == "Болдырмау"

    def test_non_utf8_arb_is_rejected(self):
        bad_fid = "iso_test_binary.arb"
        (UPLOAD_DIR / bad_fid).write_bytes(b'{\n  "key": "\xa3\xa3\xa3"\n}\n')
        try:
            with pytest.raises(HTTPException) as exc:
                asyncio.run(analyze(AnalyzeRequest(file_ids=[bad_fid])))
            assert exc.value.status_code == 400
        finally:
            (UPLOAD_DIR / bad_fid).unlink(missing_ok=True)

    def test_corrupted_arb_is_rejected(self):
        bad_fid = "iso_test_corrupt.arb"
        (UPLOAD_DIR / bad_fid).write_text("this is not an arb file", encoding="utf-8")
        try:
            with pytest.raises(HTTPException) as exc:
                asyncio.run(analyze(AnalyzeRequest(file_ids=[bad_fid])))
            assert exc.value.status_code == 400
        finally:
            (UPLOAD_DIR / bad_fid).unlink(missing_ok=True)
