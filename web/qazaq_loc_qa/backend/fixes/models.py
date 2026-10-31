from enum import Enum
from typing import Optional

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

class FixStatus(str, Enum):
    AUTO = "auto"          # applied automatically (high confidence)
    PENDING = "pending"    # needs user confirmation (medium confidence)
    MANUAL = "manual"      # requires manual review (low confidence)
    ACCEPTED = "accepted"  # user accepted a pending fix
    REJECTED = "rejected"  # user rejected a pending fix

class FixItem(BaseModel):
    """A suggested correction for a localisation error.
    Mirrors :class:`ErrorItem` fields plus the engine status.
    """
    key: str
    category: str
    source_text: str
    target_text: str
    suggested_fix: Optional[str] = None
    reason: str
    confidence: str  # "high", "medium", "low"
    status: FixStatus

    if PYDANTIC_V2:
        model_config = ConfigDict(frozen=True)

class HistoryEntry(BaseModel):
    """Record of a change applied to an ARB file.
    ``snapshot_path`` – path to the backup made before the change.
    """
    timestamp: str  # ISO‑8601
    description: str
    snapshot_path: str
    snapshot_hash: Optional[str] = None

    if PYDANTIC_V2:
        model_config = ConfigDict(frozen=True)
