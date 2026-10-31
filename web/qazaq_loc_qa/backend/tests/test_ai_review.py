import os, sys, time
import json

import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from qazaq_loc_qa.backend.ai.config_store import AIConfigStore
from qazaq_loc_qa.backend.ai import review as review_mod
from qazaq_loc_qa.backend.ai.models import ProviderConfig


class FakeProvider:
    def __init__(self, cfg, api_key=None):
        self.cfg = cfg
        self.api_key = api_key
    def test_connection(self):
        return {"ok": True, "message": "ok"}
    def chat(self, messages, model=None, temperature=0.2, max_tokens=1024, timeout=None):
        from qazaq_loc_qa.backend.ai.models import LLMResponse
        text = '[{"key":"hello","category":"grammar","source":"hi","current_translation":"bad","suggested_translation":"good","explanation":"fix","confidence":"high","severity":"medium"}]'
        return LLMResponse(text=text, prompt_tokens=10, completion_tokens=5, total_tokens=15)


def test_ai_review_mock(monkeypatch):
    store = AIConfigStore()
    cfg = {"id": "testprov", "type": "openai", "name": "t", "model": "m", "enabled": True, "timeout": 5}
    store.add_or_update(cfg)
    store.set_secret("testprov", "secret")

    monkeypatch.setattr(review_mod, "create_provider", lambda pcfg, api_key=None: FakeProvider(pcfg, api_key))
    monkeypatch.setattr(review_mod, "get_cached", lambda k: None)
    monkeypatch.setattr(review_mod, "set_cached", lambda k, v: None)
    # also patch in providers module used by review? review imports create_provider at runtime from providers
    import qazaq_loc_qa.backend.ai.providers as prov_mod
    monkeypatch.setattr(prov_mod, "create_provider", lambda pcfg, api_key=None: FakeProvider(pcfg, api_key))

    # build a minimal analysis
    analysis = {
        "file_ids": [],
        "source_file_id": None,
        "target_file_id": None,
        "files": {},
        "errors": [],
        "fixes": {},
        "history": [],
        "ai_findings": [],
        "ai_decisions": {},
    }
    # need target_arb with keys; use a simple object
    class MiniArb:
        def keys(self):
            return ["hello"]
        def get(self, k):
            return "bad"
        @property
        def locale(self):
            return "kk"
    analysis["files"] = {}
    analysis["target_file_id"] = "t"
    analysis["files"]["t"] = MiniArb()

    job_id = review_mod.start_review_job("an1", "testprov", {"max_items": 5}, analysis)
    # wait
    for _ in range(50):
        job = review_mod.get_job(job_id)
        if job.status in ("done", "error", "stopped"):
            break
        time.sleep(0.1)
    job = review_mod.get_job(job_id)
    assert job.status == "done", job.errors
    assert len(job.findings) == 1
    assert job.findings[0]["key"] == "hello"
    assert job.usage["total_tokens"] == 15

    # cleanup
    store.delete("testprov")


@pytest.mark.parametrize(
    ("locale", "expected_glossary"),
    [
        ("kk", [{"ru": "магазин", "kk": "дүкен"}]),
        ("kk-Cyrl", [{"ru": "магазин", "kk": "дүкен"}]),
        ("uz", []),
        (None, []),
    ],
)
def test_ai_review_only_sends_kazakh_glossary_for_kazakh_target(
    monkeypatch, locale, expected_glossary
):
    from qazaq_loc_qa.backend.ai.models import LLMResponse
    from qazaq_loc_qa.backend.glossary.service import glossary_service

    requests = []
    glossary_reads = []

    class ConfigStore:
        def get(self, provider_id):
            return {
                "id": provider_id,
                "type": "openai",
                "name": "test",
                "model": "test-model",
            }

        def get_secret(self, provider_id):
            return "mock-key"

    class Provider:
        def chat(self, messages, **kwargs):
            requests.append(json.loads(messages[-1]["content"]))
            return LLMResponse(text="[]")

    class MiniArb:
        def __init__(self, value, target_locale=None):
            self.value = value
            self.locale = target_locale

        def keys(self):
            return ["shop"]

        def get(self, key):
            return self.value

    source = MiniArb("магазин")
    target = MiniArb("do'kon", locale)
    analysis = {
        "files": {"source": source, "target": target},
        "source_file_id": "source",
        "target_file_id": "target",
        "ai_findings": [],
    }

    monkeypatch.setattr(review_mod, "create_provider", lambda *args, **kwargs: Provider())
    monkeypatch.setattr(review_mod, "get_cached", lambda key: None)
    monkeypatch.setattr(review_mod, "set_cached", lambda key, value: None)
    monkeypatch.setattr(review_mod, "_persist_job_state", lambda job, data: None)
    def get_glossary():
        glossary_reads.append(True)
        return {"магазин": {"kk": "дүкен", "variants": ["дүкен"]}}

    monkeypatch.setattr(glossary_service, "get_all_terms", get_glossary)

    job = review_mod.AIReviewJob(
        f"job-{locale}",
        "analysis",
        "provider",
        {"max_items": 1, "batch_size": 1},
    )
    review_mod.run_review_job(job, analysis, ConfigStore())

    assert job.status == "done"
    assert requests[0]["items"][0]["glossary"] == expected_glossary
    assert len(glossary_reads) == (1 if locale and locale.lower().startswith("kk") else 0)
