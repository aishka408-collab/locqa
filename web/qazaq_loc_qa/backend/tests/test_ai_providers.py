import io
import sys, os
import urllib.error
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from qazaq_loc_qa.backend.ai.models import ProviderConfig
from qazaq_loc_qa.backend.ai.providers import (
    OpenAIProvider, AnthropicProvider, GeminiProvider, OllamaProvider, create_provider
)


def make_cfg(ptype, model="m"):
    return ProviderConfig(id="p1", type=ptype, name="t", model=model, base_url="", timeout=5)


def test_openai_parse():
    cfg = make_cfg("openai")
    p = OpenAIProvider(cfg, api_key="key")
    p._post_json = lambda url, headers, body, timeout: {
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    r = p.chat([{"role": "user", "content": "hi"}])
    assert r.text == "hello"
    assert r.total_tokens == 5


def test_anthropic_parse():
    cfg = make_cfg("anthropic")
    p = AnthropicProvider(cfg, api_key="key")
    p._post_json = lambda url, headers, body, timeout: {
        "content": [{"type": "text", "text": "bonjour"}],
        "usage": {"input_tokens": 4, "output_tokens": 3},
    }
    r = p.chat([{"role": "user", "content": "hi"}])
    assert r.text == "bonjour"
    assert r.total_tokens == 7


def test_gemini_parse():
    cfg = make_cfg("gemini", model="gemini-1.5-flash")
    p = GeminiProvider(cfg, api_key="key")
    captured = {}

    def fake_post(url, headers, body, timeout):
        captured.update(body=body)
        return {
            "candidates": [{"content": {"parts": [{"text": "salom"}]}}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
        }

    p._post_json = fake_post
    r = p.chat([{"role": "user", "content": "hi"}])
    assert r.text == "salom"
    assert r.total_tokens == 3
    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_post_json_honors_retry_after_and_does_not_sleep_after_final_attempt(monkeypatch):
    cfg = make_cfg("openai")
    cfg.max_retries = 1
    provider = OpenAIProvider(cfg, api_key="key")
    attempts = []
    delays = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        attempts.append(request)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "temporarily unavailable",
                {"Retry-After": "0"},
                io.BytesIO(b'{"error":"unavailable"}'),
            )
        return FakeResponse()

    monkeypatch.setattr("qazaq_loc_qa.backend.ai.providers.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("qazaq_loc_qa.backend.ai.providers.time.sleep", delays.append)

    assert provider._post_json("https://example.test", {}, {}, 1) == {"ok": True}
    assert len(attempts) == 2
    assert delays == [0]

    cfg.max_retries = 0

    def fail_once(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            "temporarily unavailable",
            {"Retry-After": "5"},
            io.BytesIO(b'{"error":"unavailable"}'),
        )

    monkeypatch.setattr("qazaq_loc_qa.backend.ai.providers.urllib.request.urlopen", fail_once)
    delays.clear()
    try:
        provider._post_json("https://example.test", {}, {}, 1)
        assert False, "Expected HTTP 503 to be raised after the configured attempts"
    except RuntimeError as exc:
        assert "HTTP 503" in str(exc)
    assert delays == []


def test_ollama_parse():
    cfg = make_cfg("ollama", model="llama3.1")
    p = OllamaProvider(cfg)
    p._post_json = lambda url, headers, body, timeout: {
        "message": {"content": "salem"},
        "prompt_eval_count": 6,
        "eval_count": 4,
    }
    r = p.chat([{"role": "user", "content": "hi"}])
    assert r.text == "salem"
    assert r.total_tokens == 10


def test_create_provider_unknown():
    cfg = make_cfg("nope")
    try:
        create_provider(cfg)
        assert False
    except ValueError:
        pass


def test_test_connection_no_key():
    cfg = make_cfg("openai")
    p = OpenAIProvider(cfg, api_key="")
    # force failure by making _post_json raise
    def raise_err(*a, **k):
        raise RuntimeError("no key")
    p._post_json = raise_err
    res = p.test_connection()
    assert res["ok"] is False
