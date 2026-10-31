import json
import os
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

from qazaq_loc_qa.backend.api.state import STATE_DIR

CONFIG_PATH = STATE_DIR / "ai_providers.json"
SECRETS_PATH = STATE_DIR / "ai_secrets.json"


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Invalid JSON in AI configuration file: {path.name}") from exc


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class AIConfigStore:
    def list_configs(self) -> List[Dict[str, Any]]:
        data = _read_json(CONFIG_PATH)
        if data is None:
            return []
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError(f"AI provider configuration must be a list of objects: {CONFIG_PATH.name}")
        return data

    def save_configs(self, configs: List[Dict[str, Any]]) -> None:
        _write_json(CONFIG_PATH, configs)

    def add_or_update(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        configs = self.list_configs()
        if not cfg.get("id"):
            cfg["id"] = uuid.uuid4().hex
        found = False
        for i, c in enumerate(configs):
            if c.get("id") == cfg["id"]:
                configs[i] = cfg
                found = True
                break
        if not found:
            configs.append(cfg)
        self.save_configs(configs)
        return cfg

    def delete(self, pid: str) -> bool:
        configs = self.list_configs()
        new = [c for c in configs if c.get("id") != pid]
        if len(new) == len(configs):
            return False
        self.save_configs(new)
        self.delete_secret(pid)
        return True

    def get(self, pid: str) -> Optional[Dict[str, Any]]:
        for c in self.list_configs():
            if c.get("id") == pid:
                return c
        return None

    # Secrets stored separately (local file). For stronger security use env vars.
    def _read_secrets(self) -> Dict[str, str]:
        data = _read_json(SECRETS_PATH)
        if data is None:
            return {}
        if not isinstance(data, dict) or any(
            not isinstance(provider_id, str) or not isinstance(secret, str)
            for provider_id, secret in data.items()
        ):
            raise ValueError(f"AI secrets must be a string-to-string object: {SECRETS_PATH.name}")
        return data

    def set_secret(self, pid: str, key: str) -> None:
        secrets = self._read_secrets()
        if key:
            secrets[pid] = key
        else:
            secrets.pop(pid, None)
        _write_json(SECRETS_PATH, secrets)
        try:
            os.chmod(SECRETS_PATH, 0o600)
        except Exception:
            pass

    def get_secret(self, pid: str) -> Optional[str]:
        # Prefer environment variable if configured
        cfg = self.get(pid)
        if cfg:
            env_name = cfg.get("api_key_env")
            if env_name:
                val = os.environ.get(env_name)
                if val:
                    return val
        secrets = self._read_secrets()
        return secrets.get(pid)

    def delete_secret(self, pid: str) -> None:
        secrets = self._read_secrets()
        secrets.pop(pid, None)
        _write_json(SECRETS_PATH, secrets)

    def has_secret(self, pid: str) -> bool:
        return bool(self.get_secret(pid))
