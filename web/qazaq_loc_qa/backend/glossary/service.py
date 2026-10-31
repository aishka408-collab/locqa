"""
Glossary persistent storage and CRUD service.
Stores data in state/glossary.json.
Format:
{
    "bonus": {"kk": "бонус", "variants": ["бонус", "бонусы"]},
    "coins": {"kk": "монеталар", "variants": ["монета", "монеталар"]}
}
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Any

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
GLOSSARY_PATH = STATE_DIR / "glossary.json"


class GlossaryService:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else GLOSSARY_PATH
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> Dict[str, Dict[str, Any]]:
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError(f"Invalid glossary JSON: {self.path}") from exc
            if not isinstance(data, dict) or any(not isinstance(value, dict) for value in data.values()):
                raise ValueError(f"Glossary state must be an object of term objects: {self.path}")
            self._cache = data
        else:
            self._cache = {}
        return self._cache

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    def get_all_terms(self) -> Dict[str, Dict[str, Any]]:
        self.load()  # ensure we have fresh data
        return dict(self._cache)

    def get_term(self, ru_term: str) -> Optional[Dict[str, Any]]:
        self.load()
        return self._cache.get(ru_term)

    def add_term(self, ru_term: str, kk: str, variants: Optional[List[str]] = None) -> None:
        self.load()
        if variants is None:
            variants = [kk]
        elif kk not in variants:
            variants = [kk] + list(variants)
        self._cache[ru_term] = {
            "kk": kk,
            "variants": list(variants)
        }
        self.save()

    def update_term(self, ru_term: str, kk: str, variants: Optional[List[str]] = None) -> bool:
        self.load()
        if ru_term not in self._cache:
            return False
        if variants is None:
            variants = self._cache[ru_term].get("variants", [kk])
        self._cache[ru_term] = {
            "kk": kk,
            "variants": list(variants)
        }
        self.save()
        return True

    def delete_term(self, ru_term: str) -> bool:
        self.load()
        if ru_term in self._cache:
            del self._cache[ru_term]
            self.save()
            return True
        return False

    def clear(self) -> None:
        self._cache = {}
        self.save()


# Singleton instance
glossary_service = GlossaryService()
