from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional


@dataclass
class ProviderConfig:
    id: str
    type: str  # openai, gemini, anthropic, openai_compat, ollama
    name: str
    base_url: str = ""
    model: str = ""
    enabled: bool = True
    timeout: int = 30
    max_retries: int = 2
    batch_size: int = 8
    concurrency: int = 2
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_secret=False):
        d = asdict(self)
        if not include_secret:
            d.pop("api_key", None)
        return d


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AIFinding:
    key: str
    category: str
    source: str = ""
    current_translation: str = ""
    suggested_translation: str = ""
    explanation: str = ""
    confidence: str = "medium"
    severity: str = "medium"
    provider: str = ""
    model: str = ""
    review_status: str = "pending"

    def to_dict(self):
        return asdict(self)


AI_FINDING_SCHEMA = {
    "required": ["key", "category"],
    "optional": ["source", "current_translation", "suggested_translation",
                 "explanation", "confidence", "severity"],
}
