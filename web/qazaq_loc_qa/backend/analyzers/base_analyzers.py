# qazaq_loc_qa backend analyzers

# Base analyzers module – each analyzer returns a list of dicts with the following keys:
#   - key: the ARB key
#   - category: name of the analyzer (e.g., "untranslated")
#   - original: Russian source text
#   - current: Kazakh translation
#   - suggestion: optional auto‑suggested fix (may be None)
#   - confidence: "high", "medium", "low"
#   - explanation: human‑readable description of the problem

from __future__ import annotations

import re
from collections import Counter
from typing import List, Dict

from ..utils.arb_parser import ARBFile
from ..utils.placeholders import extract_placeholders
from .text_utils import (
    has_cyrillic,
    has_kazakh_specific,
    has_latin,
    is_abbreviation,
    is_latin_locale,
    latin_tokens_to_flag,
    meaningful_length,
    russian_signals,
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
_cyrillic_range = re.compile(r"[\u0400-\u04FF]")

def is_cyrillic(text: str) -> bool:
    return bool(_cyrillic_range.search(text))

# ---------------------------------------------------------------------------
# Analyzer implementations
# ---------------------------------------------------------------------------

def analyzer_untranslated(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Detect keys where Kazakh text is exactly equal to the Russian source.
    Returns high‑confidence entries.

    Skips empty values and legitimate exact matches for brands / names /
    numbers / Latin tokens (per project requirements, equality with source
    is acceptable for such strings and is not an error).
    """
    results = []
    for key, ru_val in ru.items():
        if key.startswith("@@"):
            continue
        kk_val = kk.get(key)
        if kk_val is None:
            continue
        if not ru_val or not kk_val:
            # Empty source or translation cannot be meaningfully judged as
            # "untranslated" – different quality issue, handled elsewhere.
            continue
        if not re.search(r"[А-Яа-яЁё]", ru_val):
            # Source contains no Cyrillic (brand names, Latin tokens, numbers) –
            # exact equality is legitimate, not an error.
            continue
        if kk_val == ru_val:
            results.append({
                "key": key,
                "category": "untranslated",
                "original": ru_val,
                "current": kk_val,
                "suggestion": None,
                "confidence": "high",
                "explanation": "Kazakh translation is identical to Russian source; likely not translated.",
            })
    return results


def analyzer_same_as_source(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Detect case‑insensitive matches that are not exact duplicates.
    Lower confidence because some strings legitimately match (brands, numbers).
    """
    results = []
    for key, ru_val in ru.items():
        if key.startswith("@@"):
            continue
        kk_val = kk.get(key)
        if kk_val is None:
            continue
        if kk_val.lower() == ru_val.lower() and kk_val != ru_val:
            results.append({
                "key": key,
                "category": "same_as_source",
                "original": ru_val,
                "current": kk_val,
                "suggestion": None,
                "confidence": "medium",
                "explanation": "Kazakh translation matches Russian text ignoring case; may be an accidental copy.",
            })
    return results


def analyzer_placeholder_mismatch(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Detect mismatched placeholder sets between source and translation.
    Suggest a fix that aligns placeholders with the source.
    """
    results = []
    for key, ru_val in ru.items():
        if key.startswith("@@"):
            continue
        kk_val = kk.get(key)
        if kk_val is None:
            continue
        ru_ph = Counter(extract_placeholders(ru_val))
        kk_ph = Counter(extract_placeholders(kk_val))
        if ru_ph != kk_ph:
            # Build a simple suggestion: keep current text but replace placeholder set
            corrected = kk_val
            # remove extra placeholders
            for ph, count in (kk_ph - ru_ph).items():
                for _ in range(count):
                    corrected = corrected.replace(ph, "", 1)
            # add missing placeholders (append at end, crude but safe)
            for ph, count in (ru_ph - kk_ph).items():
                corrected = corrected + (" " + ph) * count
            corrected = corrected.strip()
            results.append({
                "key": key,
                "category": "placeholder_mismatch",
                "original": ru_val,
                "current": kk_val,
                "suggestion": corrected,
                "confidence": "high",
                "explanation": f"Placeholder sets differ (source: {sorted(list(ru_ph))}, translation: {sorted(list(kk_ph))}).",
            })
    return results


def analyzer_wrong_language(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Detect target strings that are still (partly) in the wrong language.

    The previous implementation flagged *every* Cyrillic string that did not
    contain a Kazakh-specific letter.  That produced thousands of false
    positives because a large part of native Kazakh is written with letters
    shared with Russian (``Болдырмау``, ``Тапсырмалар``, ``Маусым`` ...).

    The refined logic uses positive evidence instead:

    * strings identical to the source are handled by ``untranslated``;
    * mixed Kazakh/Russian strings are handled by
      ``machine_translation_suspect``;
    * a pure Cyrillic string is reported only when it contains Russian
      function words, Russian-only endings or the letters ``ё``/``ъ``;
    * for Latin-script targets (``uz``/``en``) leftover Cyrillic fragments
      are reported as wrong language.
    """
    results: List[Dict] = []
    locale = getattr(kk, "locale", None)
    latin_target = is_latin_locale(locale)

    for key, ru_val in ru.items():
        if key.startswith("@@"):
            continue
        target = kk.get(key)
        if target is None or not str(target).strip():
            continue
        target = str(target)
        if ru_val and target == ru_val:
            # Exact copies are reported by analyzer_untranslated.
            continue

        if not has_cyrillic(target):
            # A fully Latin-script value in a Kazakh file is a different
            # language (Uzbek/Portuguese/English leftovers), not a spelling
            # issue.  Brand-only strings are filtered out by the whitelist.
            if not latin_target and has_latin(target):
                flagged = latin_tokens_to_flag(target)
                if len(flagged) >= 2 or (len(flagged) == 1 and len(flagged[0]) >= 6):
                    results.append({
                        "key": key,
                        "category": "wrong_language",
                        "original": ru_val,
                        "current": target,
                        "suggestion": None,
                        "confidence": "high" if len(flagged) >= 2 else "medium",
                        "explanation": (
                            "В казахском файле найдена строка на другом языке "
                            "(латиница: " + ", ".join(sorted(set(flagged))[:5]) + ")."
                        ),
                    })
            continue

        if latin_target:
            # e.g. Uzbek/English written in Latin script: a Cyrillic fragment
            # is a leftover from the Russian source.
            cyr = len(_cyrillic_range.findall(target))
            lat = len(re.findall(r"[A-Za-z]", target))
            results.append({
                "key": key,
                "category": "wrong_language",
                "original": ru_val,
                "current": target,
                "suggestion": None,
                "confidence": "high" if cyr >= 3 and cyr >= lat else "medium",
                "explanation": (
                    "Cyrillic characters found in a Latin-script target "
                    f"(locale={locale}); likely an untranslated Russian fragment."
                ),
            })
            continue

        # Kazakh target: only report strings with positive evidence of Russian.
        if has_kazakh_specific(target):
            # Mixed scripts are a partial-translation signal, not "wrong language".
            continue
        stripped = target.strip()
        if len(stripped) <= 4 or is_abbreviation(stripped):
            # Very short labels are usually language-neutral.  The exception is
            # an unmistakable Russian function word (e.g. "Профи" -> "Про"):
            # it is not an abbreviation and cannot be native Kazakh.  Ambiguous
            # short forms such as "он" (Kazakh "ten") are deliberately excluded.
            short_russian = {
                "про", "для", "без", "при", "над", "под", "что", "это",
                "или", "как", "где", "кто", "чем", "уже", "еще", "ещё",
                "всё", "все",
            }
            if stripped.casefold() not in short_russian:
                continue
            if is_abbreviation(stripped) or has_kazakh_specific(stripped):
                continue
            results.append({
                "key": key,
                "category": "wrong_language",
                "original": ru_val,
                "current": target,
                "suggestion": None,
                "confidence": "medium",
                "explanation": (
                    "Короткая строка является русским служебным словом "
                    f"('{stripped}'), а не казахским переводом."
                ),
            })
            continue
        if has_latin(target) and not russian_signals(target)[0]:
            # Latin-dominant strings are covered by orthography, not here.
            continue

        stop, endings, strong = russian_signals(stripped)
        if not (stop or endings or strong):
            continue

        evidence = []
        if stop:
            evidence.append("служебные слова: " + ", ".join(sorted(set(stop))[:4]))
        if endings:
            evidence.append("русские окончания: " + ", ".join(sorted(set(endings))[:4]))
        if strong:
            evidence.append("буквы ё/ъ")
        confidence = "high" if (len(stop) + len(endings) >= 2 or strong) else "medium"
        results.append({
            "key": key,
            "category": "wrong_language",
            "original": ru_val,
            "current": target,
            "suggestion": None,
            "confidence": confidence,
            "explanation": (
                "Строка выглядит как русский текст, а не казахский ("
                + "; ".join(evidence) + ")."
            ),
        })
    return results


def analyzer_length_overflow(ru: ARBFile, kk: ARBFile, threshold: float = 1.3) -> List[Dict]:
    """Detect translations that are significantly longer than the source.

    Character-count ratio alone is very noisy for very short strings
    (``Порох`` → ``Оқ дәрі`` is a perfectly normal translation).  The refined
    check therefore uses *visible* length (placeholders/tags/spaces removed),
    a length-dependent ratio and a minimum absolute growth.
    """
    results = []
    for key, ru_val in ru.items():
        if key.startswith("@@"):
            continue
        kk_val = kk.get(key)
        if kk_val is None:
            continue
        src_len = meaningful_length(ru_val)
        tgt_len = meaningful_length(kk_val)
        if src_len < 5 or tgt_len <= src_len:
            continue

        locale = getattr(kk, "locale", None)
        latin_target = False
        try:
            from .text_utils import is_latin_locale
            latin_target = is_latin_locale(locale)
        except Exception:
            latin_target = False

        if latin_target:
            # Agglutinative Latin-script targets (Uzbek) legitimately grow
            # more than Kazakh; use a slightly looser threshold to avoid
            # noisy false positives while keeping genuine overflows.
            if src_len <= 15:
                effective, min_delta = max(threshold, 1.7), 4
            elif src_len <= 40:
                effective, min_delta = max(threshold, 1.55), 6
            else:
                effective, min_delta = max(threshold, 1.5), 9
        elif src_len <= 15:
            effective, min_delta = max(threshold, 1.6), 3
        elif src_len <= 40:
            effective, min_delta = max(threshold, 1.45), 5
        else:
            effective, min_delta = max(threshold, 1.4), 8

        if tgt_len <= src_len * effective or (tgt_len - src_len) < min_delta:
            continue

        ratio = tgt_len / src_len
        results.append({
            "key": key,
            "category": "length_overflow",
            "original": ru_val,
            "current": kk_val,
            "suggestion": None,
            "confidence": "medium" if ratio >= 2.0 else "low",
            "explanation": (
                f"Возможное переполнение UI: перевод длиннее исходника на "
                f"{tgt_len - src_len} симв. (×{ratio:.2f}; порог ×{effective:.2f}). "
                "Проверьте вёрстку вручную."
            ),
        })
    return results

# ---------------------------------------------------------------------------
# Aggregator – runs all base analyzers and returns a combined list
# ---------------------------------------------------------------------------

def run_base_analyzers(ru_path: str, kk_path: str) -> List[Dict]:
    ru = ARBFile.load(ru_path)
    kk = ARBFile.load(kk_path)
    results: List[Dict] = []
    results.extend(analyzer_untranslated(ru, kk))
    results.extend(analyzer_same_as_source(ru, kk))
    results.extend(analyzer_placeholder_mismatch(ru, kk))
    results.extend(analyzer_wrong_language(ru, kk))
    results.extend(analyzer_length_overflow(ru, kk))
    return results

# ---------------------------------------------------------------------------
# Example of how the module could be used from an API endpoint:
# ---------------------------------------------------------------------------
# from fastapi import APIRouter, UploadFile, File
# router = APIRouter()
#
# @router.post("/analyze/base")
# async def analyze_base(ru: UploadFile = File(...), kk: UploadFile = File(...)):
#     ru_path = f"/tmp/{ru.filename}"
#     kk_path = f"/tmp/{kk.filename}"
#     with open(ru_path, "wb") as f:
#         f.write(await ru.read())
#     with open(kk_path, "wb") as f:
#         f.write(await kk.read())
#     findings = run_base_analyzers(ru_path, kk_path)
#     return {"findings": findings}
