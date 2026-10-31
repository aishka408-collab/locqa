import json
from pathlib import Path
from typing import Dict, Any

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
ANALYSES_PATH = STATE_DIR / "analyses.json"
CURRENT_SCHEMA_VERSION = 1


def load_analyses() -> Dict[str, Any]:
    if not ANALYSES_PATH.is_file():
        return {}
    try:
        content = json.loads(ANALYSES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Persisted analysis state is invalid: {ANALYSES_PATH}") from exc

    if isinstance(content, dict) and "_schema_version" in content and "data" in content:
        data = content["data"]
        if not isinstance(data, dict):
            raise ValueError(f"Persisted analysis state has an invalid data object: {ANALYSES_PATH}")
        return data
    if isinstance(content, dict):
        return content
    raise ValueError(f"Persisted analysis state must be a JSON object: {ANALYSES_PATH}")


def save_analyses(data: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    wrapped = {
        "_schema_version": CURRENT_SCHEMA_VERSION,
        "data": data
    }
    # Atomic write via temporary file
    temp_path = ANALYSES_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(ANALYSES_PATH)
