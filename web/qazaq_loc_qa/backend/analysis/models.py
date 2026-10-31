from typing import Dict, Optional

try:
    from pydantic import BaseModel, ConfigDict
    PYDANTIC_V2 = True
except ImportError:
    PYDANTIC_V2 = False
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def dict(self):
            return dict(self.__dict__)

class ErrorItem(BaseModel):
    """Unified error representation for analysis results.
    Fields correspond to the API contract.
    """
    key: str
    category: str
    source_text: str
    target_text: str
    suggested_fix: Optional[str] = None
    reason: str
    confidence: str  # "high", "medium" or "low"

    if PYDANTIC_V2:
        model_config = ConfigDict(frozen=True)

# Alias for a map of file identifiers to ARBFile objects
ARBFilesMap = Dict[str, "ARBFile"]  # forward reference; actual type imported where used
