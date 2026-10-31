import hashlib
import json
from pathlib import Path

from qazaq_loc_qa.backend.api.state import STATE_DIR

CACHE_PATH = STATE_DIR / "ai_cache.json"


def _read():
    if not CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(data):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def cache_key(provider_id, model, key, source, current, target_locale, glossary_hash,
              provider_context=""):
    raw = json.dumps(
        [provider_id, model, key, source, current, target_locale, glossary_hash, provider_context],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached(k):
    data = _read()
    return data.get(k)


def set_cached(k, value):
    data = _read()
    data[k] = value
    # keep cache bounded
    if len(data) > 5000:
        # drop oldest-ish (dict insertion order)
        keys = list(data.keys())
        for kk in keys[:len(keys) - 4000]:
            data.pop(kk, None)
    _write(data)
