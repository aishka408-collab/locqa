import asyncio
import csv
import io
import json
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from openpyxl import load_workbook

import qazaq_loc_qa.backend.ai.config_store as config_store
import qazaq_loc_qa.backend.ai.providers as providers
import qazaq_loc_qa.backend.ai.review as review
import qazaq_loc_qa.backend.api.main as api
import qazaq_loc_qa.backend.api.state as state
import qazaq_loc_qa.backend.routers.analysis as legacy_analysis
from qazaq_loc_qa.backend.ai.cache import cache_key
from qazaq_loc_qa.backend.ai.models import LLMResponse, ProviderConfig
from qazaq_loc_qa.backend.analyzers.kazakh_analyzers import analyzer_plural_mismatch
from qazaq_loc_qa.backend.fixes.engine import extract_placeholders, validate_fix
from qazaq_loc_qa.backend.glossary.service import GlossaryService
from qazaq_loc_qa.backend.utils.arb_parser import ARBEntry, ARBFile


def _arb(value, locale=None):
    entries = []
    if locale:
        entries.append(ARBEntry("comment", "@@locale", locale, ""))
    entries.append(ARBEntry("kv", "items", value, ""))
    return ARBFile(entries)


def test_provider_keys_use_headers_not_url(monkeypatch):
    captured = {}

    def capture_openai(url, headers, body, timeout):
        captured.update(url=url, headers=headers)
        return {"choices": [{"message": {"content": "ok"}}]}

    def capture_gemini(url, headers, body, timeout):
        captured.update(url=url, headers=headers)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    openai = providers.OpenAIProvider(
        ProviderConfig(id="openai", type="openai", name="OpenAI", model="test-model"),
        api_key="test-openai-key",
    )
    monkeypatch.setattr(openai, "_post_json", capture_openai)
    openai.chat([{"role": "user", "content": "ping"}])
    assert captured["headers"]["Authorization"] == "Bearer test-openai-key"

    gemini = providers.GeminiProvider(
        ProviderConfig(id="gemini", type="gemini", name="Gemini", model="test-model"),
        api_key="test-gemini-key",
    )
    monkeypatch.setattr(gemini, "_post_json", capture_gemini)
    gemini.chat([{"role": "user", "content": "ping"}])
    assert captured["headers"]["x-goog-api-key"] == "test-gemini-key"
    assert "test-gemini-key" not in captured["url"]


def test_provider_request_shapes_are_mocked_for_all_adapters(monkeypatch):
    observed = {}
    replies = {
        "openai": {"choices": [{"message": {"content": "ok"}}]},
        "openai_compat": {"choices": [{"message": {"content": "ok"}}]},
        "anthropic": {"content": [{"type": "text", "text": "ok"}]},
        "gemini": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        "ollama": {"message": {"content": "ok"}},
    }

    def exercise(name, provider_type, provider_class, base_url, api_key):
        config = ProviderConfig(
            id=name,
            type=provider_type,
            name=name,
            model="test-model",
            base_url=base_url,
        )
        provider = provider_class(config, api_key=api_key)

        def capture(url, headers, body, timeout):
            observed[name] = (url, headers, body)
            return replies[name]

        monkeypatch.setattr(provider, "_post_json", capture)
        provider.chat([
            {"role": "system", "content": "system instructions"},
            {"role": "user", "content": "user text"},
        ])

    exercise("openai", "openai", providers.OpenAIProvider, "", "openai-test-key")
    exercise(
        "openai_compat",
        "openai_compat",
        providers.OpenAICompatProvider,
        "https://example.test/v1",
        "compat-test-key",
    )
    exercise("anthropic", "anthropic", providers.AnthropicProvider, "", "anthropic-test-key")
    exercise("gemini", "gemini", providers.GeminiProvider, "", "gemini-test-key")
    exercise("ollama", "ollama", providers.OllamaProvider, "", "")

    assert observed["openai"][0].endswith("/v1/chat/completions")
    assert observed["openai"][1]["Authorization"] == "Bearer openai-test-key"
    assert observed["openai_compat"][0] == "https://example.test/v1/chat/completions"
    assert observed["openai_compat"][1]["Authorization"] == "Bearer compat-test-key"
    assert observed["anthropic"][0].endswith("/v1/messages")
    assert observed["anthropic"][1]["x-api-key"] == "anthropic-test-key"
    assert observed["anthropic"][1]["anthropic-version"] == "2023-06-01"
    assert observed["anthropic"][2]["system"] == "system instructions"
    assert observed["gemini"][0].endswith("/v1beta/models/test-model:generateContent")
    assert observed["gemini"][1]["x-goog-api-key"] == "gemini-test-key"
    assert "gemini-test-key" not in observed["gemini"][0]
    assert observed["gemini"][2]["systemInstruction"]["parts"][0]["text"] == "system instructions"
    assert observed["gemini"][2]["generationConfig"]["responseMimeType"] == "application/json"
    assert observed["ollama"][0] == "http://localhost:11434/api/chat"
    assert observed["ollama"][1].get("Authorization") is None
    assert observed["ollama"][2]["stream"] is False


def test_icu_placeholders_compare_arguments_not_translated_branches():
    source = "{count, plural, one{One {name}} other{{name} has # items}}"
    translated = "{count, plural, one{Un {name}} other{{name} tiene # elementos}}"

    assert extract_placeholders(source) == ["{count}", "{name}", "{name}", "#"]
    assert validate_fix(source, translated)
    assert not validate_fix(source, translated.replace("{name}", "{user}", 1))


def test_compact_arb_json_parses_and_round_trips_nested_metadata(tmp_path):
    source_path = tmp_path / "compact.arb"
    fixed_path = tmp_path / "fixed.arb"
    source_path.write_text(
        '{"@@locale":"kk","items":"A {count}","@items":{"description":"A description",'
        '"placeholders":{"count":{"type":"int"}}}}',
        encoding="utf-8",
    )

    arb = ARBFile.load(source_path)

    assert arb.locale == "kk"
    assert arb.keys() == ["items"]
    assert arb.get("items") == "A {count}"
    arb.set("items", "B {count}")
    arb.dump(fixed_path)

    restored = json.loads(fixed_path.read_text(encoding="utf-8"))
    assert restored["items"] == "B {count}"
    assert restored["@items"] == {
        "description": "A description",
        "placeholders": {"count": {"type": "int"}},
    }
    assert ARBFile.load(fixed_path).keys() == ["items"]


def test_cache_key_includes_provider_context_and_unambiguous_fields():
    first = cache_key("id", "model", "key|source", "current", "target", "kk", "glossary")
    second = cache_key("id", "model", "key", "source|current", "target", "kk", "glossary")
    assert first != second
    assert cache_key("id", "model", "key", "source", "current", "kk", "g", "openai") != (
        cache_key("id", "model", "key", "source", "current", "kk", "g", "ollama")
    )


def test_ai_review_caps_output_tokens_to_remaining_budget(monkeypatch):
    class MiniArb:
        locale = "kk"

        def keys(self):
            return ["first", "second"]

        def get(self, key):
            return "text"

    class ConfigStore:
        def get(self, provider_id):
            return {
                "id": provider_id,
                "type": "ollama",
                "name": "local",
                "model": "test-model",
                "batch_size": 1,
                "timeout": 1,
            }

        def get_secret(self, provider_id):
            return None

    class FakeProvider:
        def __init__(self):
            self.max_tokens = []
            self.responses = [
                LLMResponse(text="[]", prompt_tokens=5, completion_tokens=2, total_tokens=7),
                LLMResponse(text="[]", prompt_tokens=1, completion_tokens=2, total_tokens=3),
            ]

        def chat(self, messages, model=None, temperature=0.2, max_tokens=1024, timeout=None):
            self.max_tokens.append(max_tokens)
            return self.responses[len(self.max_tokens) - 1]

    fake_provider = FakeProvider()
    monkeypatch.setattr(review, "create_provider", lambda *args, **kwargs: fake_provider)
    monkeypatch.setattr(review, "get_cached", lambda key: None)
    monkeypatch.setattr(review, "set_cached", lambda key, value: None)
    analysis = {
        "files": {"target": MiniArb()},
        "source_file_id": None,
        "target_file_id": "target",
        "ai_findings": [],
    }
    persisted_states = []
    monkeypatch.setattr(
        api,
        "_persist",
        lambda: persisted_states.append(
            (analysis.get("ai_job_status"), analysis.get("ai_job_processed"), analysis.get("ai_job_total"))
        ),
    )
    job = review.AIReviewJob(
        "job",
        "analysis",
        "provider",
        {"max_items": 2, "batch_size": 1, "max_tokens_budget": 10},
    )

    review.run_review_job(job, analysis, ConfigStore())

    assert fake_provider.max_tokens == [10, 3]
    assert job.usage["total_tokens"] == 10
    assert job.status == "stopped"
    assert ("running", 1, 2) in persisted_states
    assert persisted_states[-1] == ("stopped", 2, 2)


def test_ai_review_marks_all_failed_provider_batches_as_error(monkeypatch):
    class MiniArb:
        locale = "kk"

        def keys(self):
            return ["first"]

        def get(self, key):
            return "text"

    class ConfigStore:
        def get(self, provider_id):
            return {
                "id": provider_id,
                "type": "ollama",
                "name": "local",
                "model": "test-model",
                "batch_size": 1,
                "timeout": 1,
            }

        def get_secret(self, provider_id):
            return None

    class FailedProvider:
        def chat(self, *args, **kwargs):
            raise ValueError("invalid provider URL")

    monkeypatch.setattr(review, "create_provider", lambda *args, **kwargs: FailedProvider())
    monkeypatch.setattr(review, "get_cached", lambda key: None)
    monkeypatch.setattr(review.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(api, "_persist", lambda: None)
    analysis = {
        "files": {"target": MiniArb()},
        "source_file_id": None,
        "target_file_id": "target",
        "ai_findings": [],
    }
    job = review.AIReviewJob(
        "failed-job",
        "analysis",
        "provider",
        {"max_items": 1, "batch_size": 1},
    )

    review.run_review_job(job, analysis, ConfigStore())

    assert job.status == "error"
    assert job.processed == 1
    assert job.usage["total_tokens"] == 0
    assert "Retry failed: invalid provider URL" in job.errors
    assert analysis["ai_job_status"] == "error"
    assert analysis["ai_job_errors"] == job.errors


def test_ai_review_restores_interrupted_job_as_stopped(tmp_path, monkeypatch):
    stored = {
        "analysis": {
            "file_ids": ["missing.arb"],
            "ai_job_id": "interrupted-job",
            "ai_job_status": "running",
            "ai_job_processed": 1,
            "ai_job_total": 3,
            "ai_findings": [{"key": "items"}],
            "ai_job_usage": {"total_tokens": 7},
        }
    }
    runtime = {}
    monkeypatch.setattr(api, "analyses_runtime", runtime)
    monkeypatch.setattr(api, "load_analyses", lambda: stored)
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(api, "_persist", lambda: None)

    api._restore_runtime()

    restored = runtime["analysis"]
    assert restored["ai_job_status"] == "stopped"
    assert restored["ai_job_processed"] == 1
    assert restored["ai_job_total"] == 3
    assert restored["ai_findings"] == [{"key": "items"}]
    assert "interrupted by server restart" in restored["ai_job_errors"][0]
    assert restored["restore_errors"] == ["Persisted ARB file is missing: 'missing.arb'"]
    status = asyncio.run(api.ai_review_status("interrupted-job"))
    assert status["status"] == "stopped"
    assert status["processed"] == 1
    assert status["total"] == 3


def test_mock_ai_accept_export_apply_and_undo(tmp_path, monkeypatch):
    source_original = '{\n  "@@locale": "ru",\n  "items": "У вас {count} предметов"\n}\n'
    target_original = '{\n  "@@locale": "kk",\n  "items": "Доступно {count} предмета"\n}\n'
    upload_dir = tmp_path / "uploads"
    backup_dir = tmp_path / "backups"
    upload_dir.mkdir()
    backup_dir.mkdir()
    source_path = upload_dir / "source.arb"
    target_path = upload_dir / "target.arb"
    source_path.write_text(source_original, encoding="utf-8")
    target_path.write_text(target_original, encoding="utf-8")
    source = ARBFile.load(source_path)
    target = ARBFile.load(target_path)
    monkeypatch.setattr(api, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(api, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(api, "_persist", lambda: None)

    class ConfigStore:
        def get(self, provider_id):
            return {
                "id": provider_id,
                "type": "ollama",
                "name": "local",
                "model": "test-model",
                "batch_size": 1,
                "timeout": 1,
            }

        def get_secret(self, provider_id):
            return None

    class FakeProvider:
        def chat(self, messages, model=None, temperature=0.2, max_tokens=1024, timeout=None):
            response = [{
                "key": "items",
                "category": "grammar",
                "source": "У вас {count} предметов",
                "current_translation": "Доступно {count} предмета",
                "suggested_translation": "Доступно {count} предмет",
                "explanation": "Mocked review finding",
                "confidence": "high",
                "severity": "medium",
            }]
            return LLMResponse(
                text=json.dumps(response, ensure_ascii=False),
                prompt_tokens=4,
                completion_tokens=5,
                total_tokens=9,
            )

    monkeypatch.setattr(review, "create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr(review, "get_cached", lambda key: None)
    monkeypatch.setattr(review, "set_cached", lambda key, value: None)
    analysis_id = "mock-ai-flow"
    analysis = {
        "file_ids": ["source.arb", "target.arb"],
        "source_file_id": "source.arb",
        "target_file_id": "target.arb",
        "files": {"source.arb": source, "target.arb": target},
        "errors": [],
        "fixes": {},
        "history": [],
        "ai_findings": [],
    }
    monkeypatch.setitem(api.analyses_runtime, analysis_id, analysis)
    job = review.AIReviewJob("mock-job", analysis_id, "local-provider", {"max_items": 1, "batch_size": 1})

    review.run_review_job(job, analysis, ConfigStore())
    assert job.status == "done"
    assert analysis["ai_findings"][0]["suggested_translation"] == "Доступно {count} предмет"

    with pytest.raises(HTTPException) as unsupported_action:
        asyncio.run(api.ai_finding_decision(analysis_id, {"key": "items", "action": "ignore"}))
    assert unsupported_action.value.status_code == 422
    with pytest.raises(HTTPException) as missing_manual_value:
        asyncio.run(api.ai_finding_decision(analysis_id, {"key": "items", "action": "manual"}))
    assert missing_manual_value.value.status_code == 422

    asyncio.run(api.ai_finding_decision(analysis_id, {"key": "items", "action": "accept"}))
    expected = "Доступно {count} предмет"
    assert analysis["fixes"]["items"]["new_value"] == expected
    assert analysis["ai_decisions"]["items"]["new_value"] == expected

    csv_rows = list(csv.reader(io.StringIO(asyncio.run(api.export_csv(analysis_id)).body.decode("utf-8"))))
    assert csv_rows[1][1] == "ai_grammar"
    assert csv_rows[1][5] == expected
    excel = load_workbook(io.BytesIO(asyncio.run(api.export_excel(analysis_id)).body))
    assert excel["Errors"]["B2"].value == "ai_grammar"
    assert excel["Errors"]["F2"].value == expected
    markdown = asyncio.run(api.export_markdown(analysis_id)).body.decode("utf-8")
    assert "ai_grammar" in markdown
    report = json.loads(asyncio.run(api.get_report(analysis_id)).body)
    assert report["ai_findings"][0]["key"] == "items"
    assert report["ai_decisions"]["items"]["new_value"] == expected

    asyncio.run(api.apply_fixes(analysis_id))
    assert ARBFile.load(upload_dir / "fixed_target.arb").get("items") == expected
    assert target_path.read_text(encoding="utf-8") == target_original

    asyncio.run(api.undo_last(analysis_id))
    assert ARBFile.load(upload_dir / "fixed_target.arb").get("items") == "Доступно {count} предмета"
    assert analysis["history"] == []


def test_legacy_router_uses_private_temp_names_and_cleans_up(monkeypatch):
    observed = {}

    def analyzer(ru_path, kk_path):
        observed["ru_path"] = ru_path
        observed["kk_path"] = kk_path
        observed["ru_text"] = Path(ru_path).read_text(encoding="utf-8")
        observed["kk_text"] = Path(kk_path).read_text(encoding="utf-8")
        return []

    monkeypatch.setattr(legacy_analysis, "run_base_analyzers", analyzer)
    ru = UploadFile(filename="../../outside-ru.arb", file=io.BytesIO(b"ru"))
    kk = UploadFile(filename="..\\outside-kk.arb", file=io.BytesIO(b"kk"))

    response = asyncio.run(legacy_analysis.analyze_base(ru, kk))

    assert response.status_code == 200
    assert observed["ru_text"] == "ru"
    assert observed["kk_text"] == "kk"
    assert Path(observed["ru_path"]).name == "ru.arb"
    assert Path(observed["kk_path"]).name == "kk.arb"
    assert not Path(observed["ru_path"]).parent.exists()


def test_legacy_router_rejects_oversized_upload(monkeypatch):
    monkeypatch.setattr(legacy_analysis, "MAX_UPLOAD_BYTES", 3)
    ru = UploadFile(filename="ru.arb", file=io.BytesIO(b"ru"))
    kk = UploadFile(filename="kk.arb", file=io.BytesIO(b"four"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(legacy_analysis._run_analyzer(ru, kk, lambda *_: []))

    assert exc_info.value.status_code == 413


def test_kazakh_plural_rule_is_not_applied_to_uzbek():
    source = _arb("{count} items")
    uzbek = _arb("{count} ta element", locale="uz")
    kazakh = _arb("{count} зат", locale="kk")

    assert analyzer_plural_mismatch(source, uzbek) == []
    assert len(analyzer_plural_mismatch(source, kazakh)) == 1


def test_corrupt_persisted_analysis_state_is_not_treated_as_empty(tmp_path, monkeypatch):
    state_path = tmp_path / "analyses.json"
    state_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(state, "ANALYSES_PATH", state_path)

    with pytest.raises(RuntimeError, match="Persisted analysis state is invalid"):
        state.load_analyses()
    assert state_path.read_text(encoding="utf-8") == "{broken"


def test_corrupt_ai_and_glossary_files_are_not_overwritten(tmp_path, monkeypatch):
    providers_path = tmp_path / "ai_providers.json"
    secrets_path = tmp_path / "ai_secrets.json"
    providers_path.write_text("{broken", encoding="utf-8")
    secrets_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_PATH", providers_path)
    monkeypatch.setattr(config_store, "SECRETS_PATH", secrets_path)

    store = config_store.AIConfigStore()
    with pytest.raises(RuntimeError, match="Invalid JSON"):
        store.list_configs()
    with pytest.raises(RuntimeError, match="Invalid JSON"):
        store.set_secret("provider", "test-secret")
    assert providers_path.read_text(encoding="utf-8") == "{broken"
    assert secrets_path.read_text(encoding="utf-8") == "{broken"

    glossary_path = tmp_path / "glossary.json"
    glossary_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Invalid glossary JSON"):
        GlossaryService(glossary_path)
    assert glossary_path.read_text(encoding="utf-8") == "{broken"


def test_analysis_rejects_same_source_and_target_without_mutating_file(tmp_path, monkeypatch):
    file_id = "same.arb"
    original = '{\n  "hello": "Привет"\n}\n'
    file_path = tmp_path / file_id
    file_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path)
    request = api.AnalyzeRequest(
        file_ids=[file_id],
        source_file_id=file_id,
        target_file_id=file_id,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.analyze(request))

    assert exc.value.status_code == 400
    assert file_path.read_text(encoding="utf-8") == original


def test_ai_review_rejects_zero_batch_size(monkeypatch):
    class ConfigStore:
        def get(self, provider_id):
            return {"id": provider_id, "enabled": True}

    monkeypatch.setattr(api, "_cfg_store", ConfigStore())
    monkeypatch.setitem(api.analyses_runtime, "audit-analysis", {"files": {}, "errors": []})
    monkeypatch.setattr(
        api,
        "start_review_job",
        lambda *args: pytest.fail("invalid options must not start a worker"),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.ai_start_review(
            "audit-analysis",
            {"provider_id": "test-provider", "batch_size": 0},
        ))

    assert exc.value.status_code == 422


def test_csv_and_excel_exports_neutralize_formula_cells(monkeypatch):
    analysis_id = "audit-export"
    data = {
        "errors": [{
            "key": "=1+1",
            "category": "@formula",
            "confidence": "high",
            "source": "+source",
            "current": "-current",
            "suggestion": "@suggestion",
            "explanation": "  =expression",
        }],
        "files": {},
    }
    monkeypatch.setitem(api.analyses_runtime, analysis_id, data)

    csv_response = asyncio.run(api.export_csv(analysis_id))
    csv_rows = list(csv.reader(io.StringIO(csv_response.body.decode("utf-8"))))
    assert csv_rows[1][0] == "'=1+1"
    assert csv_rows[1][1] == "'@formula"
    assert csv_rows[1][3] == "'+source"

    excel_response = asyncio.run(api.export_excel(analysis_id))
    workbook = load_workbook(io.BytesIO(excel_response.body), data_only=False)
    cells = workbook["Errors"][2]
    assert [cell.value for cell in cells[:6]] == [
        "'=1+1", "'@formula", "high", "'+source", "'-current", "'@suggestion",
    ]
    assert all(cell.data_type == "s" for cell in cells)
