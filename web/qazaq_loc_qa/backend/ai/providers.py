import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional

from qazaq_loc_qa.backend.ai.models import LLMResponse


class BaseLLMProvider(ABC):
    def __init__(self, config, api_key: Optional[str] = None):
        self.config = config
        self.api_key = api_key or ""

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        ...

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], model: Optional[str] = None,
            temperature: float = 0.2, max_tokens: int = 1024,
            timeout: Optional[int] = None) -> LLMResponse:
        ...

    def _post_json(self, url, headers, body, timeout):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        last_err = None
        retries = getattr(self.config, "max_retries", 2)
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.config.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                body_txt = ""
                try:
                    body_txt = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                last_err = RuntimeError(f"HTTP {e.code}: {body_txt[:300]}")
                if (e.code == 429 or 500 <= e.code < 600) and attempt < retries:
                    retry_after = (e.headers or {}).get("Retry-After")
                    time.sleep(self._retry_delay(retry_after, attempt))
                    continue
                raise last_err
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise
        raise last_err if last_err else RuntimeError("request failed")

    @staticmethod
    def _retry_delay(retry_after, attempt):
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = min(2 ** attempt, 8)
            return min(max(delay, 0), 30)
        return min(2 ** attempt, 8)


def _default_base(config, default):
    return (config.base_url or default).rstrip("/")


class OpenAIProvider(BaseLLMProvider):
    def test_connection(self):
        try:
            self.chat([{"role": "user", "content": "ping"}], max_tokens=5, timeout=15)
            return {"ok": True, "message": "Connected"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def chat(self, messages, model=None, temperature=0.2, max_tokens=1024, timeout=None):
        base = _default_base(self.config, "https://api.openai.com/v1")
        url = base + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        raw = self._post_json(url, headers, body, timeout)
        text = ""
        try:
            text = raw["choices"][0]["message"]["content"]
        except Exception:
            text = ""
        usage = raw.get("usage", {}) or {}
        return LLMResponse(
            text=text,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            raw=raw,
        )


class OpenAICompatProvider(OpenAIProvider):
    """OpenAI-compatible (OpenRouter, etc.)."""


class AnthropicProvider(BaseLLMProvider):
    def test_connection(self):
        try:
            self.chat([{"role": "user", "content": "ping"}], max_tokens=5, timeout=15)
            return {"ok": True, "message": "Connected"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def chat(self, messages, model=None, temperature=0.2, max_tokens=1024, timeout=None):
        base = _default_base(self.config, "https://api.anthropic.com")
        url = base + "/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        # convert messages to anthropic format (system separate)
        system = ""
        msgs = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                msgs.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        body = {
            "model": model or self.config.model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system
        raw = self._post_json(url, headers, body, timeout)
        text = ""
        try:
            for block in raw.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
        except Exception:
            text = ""
        usage = raw.get("usage", {}) or {}
        return LLMResponse(
            text=text,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            raw=raw,
        )


class GeminiProvider(BaseLLMProvider):
    def test_connection(self):
        try:
            self.chat([{"role": "user", "content": "ping"}], max_tokens=5, timeout=15)
            return {"ok": True, "message": "Connected"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def chat(self, messages, model=None, temperature=0.2, max_tokens=1024, timeout=None):
        model = model or self.config.model or "gemini-1.5-flash"
        base = _default_base(self.config, "https://generativelanguage.googleapis.com")
        url = f"{base}/v1beta/models/{model}:generateContent"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        # build contents
        contents = []
        system_text = ""
        for m in messages:
            if m.get("role") == "system":
                system_text += m.get("content", "") + "\n"
            else:
                role = "user" if m.get("role") != "assistant" else "model"
                contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        if system_text.strip():
            body["systemInstruction"] = {"parts": [{"text": system_text.strip()}]}
        raw = self._post_json(url, headers, body, timeout)
        text = ""
        try:
            for cand in raw.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    text += part.get("text", "")
        except Exception:
            text = ""
        usage = raw.get("usageMetadata", {}) or {}
        return LLMResponse(
            text=text,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
            raw=raw,
        )


class OllamaProvider(BaseLLMProvider):
    def test_connection(self):
        try:
            self.chat([{"role": "user", "content": "ping"}], max_tokens=5, timeout=15)
            return {"ok": True, "message": "Connected"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def chat(self, messages, model=None, temperature=0.2, max_tokens=1024, timeout=None):
        base = _default_base(self.config, "http://localhost:11434")
        url = base + "/api/chat"
        body = {
            "model": model or self.config.model or "llama3.1",
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        raw = self._post_json(url, {"Content-Type": "application/json"}, body, timeout)
        text = ""
        try:
            text = raw.get("message", {}).get("content", "")
        except Exception:
            text = ""
        return LLMResponse(
            text=text,
            prompt_tokens=raw.get("prompt_eval_count", 0),
            completion_tokens=raw.get("eval_count", 0),
            total_tokens=raw.get("prompt_eval_count", 0) + raw.get("eval_count", 0),
            raw=raw,
        )


PROVIDER_TYPES = {
    "openai": OpenAIProvider,
    "openai_compat": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


def create_provider(config, api_key=None):
    cls = PROVIDER_TYPES.get(config.type)
    if not cls:
        raise ValueError(f"Unknown provider type: {config.type}")
    return cls(config, api_key=api_key)
