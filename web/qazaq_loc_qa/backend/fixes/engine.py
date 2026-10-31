from collections import Counter
from ..utils.placeholders import extract_placeholders


def placeholders_match(original: str, suggested: str) -> bool:
    """Return ``True`` if *suggested* contains exactly the same placeholders
    (including quantity) as *original*.

    The comparison is performed on a *multiset* basis – ``Counter`` is used so
    repeated placeholders are taken into account.
    """
    return Counter(extract_placeholders(original)) == Counter(extract_placeholders(suggested))

# ---------------------------------------------------------------------------
# Simple validation wrapper used by the API (both automatic and manual fixes)
# ---------------------------------------------------------------------------

def validate_fix(original: str, suggested: str) -> bool:
    """Validate that a suggested fix does not break placeholder integrity.

    Returns ``True`` if the fix is safe, ``False`` otherwise.
    """
    return placeholders_match(original, suggested)
