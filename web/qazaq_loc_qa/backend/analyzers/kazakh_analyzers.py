import re
from collections import defaultdict
from typing import List, Dict, Set, Optional, Tuple

from ..utils.arb_parser import ARBFile
from .text_utils import (
    BACK_VOWELS,
    CYRILLIC_RE,
    FRONT_VOWELS,
    GLIDE_FINAL_LETTERS,
    cyrillic_tokens,
    has_cyrillic,
    has_kazakh_specific,
    is_latin_locale,
    last_vowel,
    latin_tokens_to_flag,
    normalize_for_compare,
    russian_signals,
)

# ---------------------------------------------------------------------------
# 1. suffix_harmony – проверка гармонии гласных
# ---------------------------------------------------------------------------
# Pair of (suffix, required vowel harmony).  Longest suffixes first so that
# ``-дар`` is preferred over ``-ар``.
_SUFFIXES: List[Tuple[str, str]] = sorted(
    [
        ("дар", "back"), ("дер", "front"), ("лар", "back"), ("лер", "front"),
        ("тар", "back"), ("тер", "front"),
        ("ға", "back"), ("ге", "front"), ("қа", "back"), ("ке", "front"),
        ("да", "back"), ("де", "front"), ("та", "back"), ("те", "front"),
        ("нан", "back"), ("нен", "front"),
    ],
    key=lambda item: -len(item[0]),
)

# Common roots that merely *look* like stem + suffix.  Keeping this list small
# and explicit is safer than guessing morphology for arbitrary loanwords.
_HARMONY_BLACKLIST = {
    "монета", "карта", "лента", "квота", "плата", "нота", "рота",
    "дата", "вата", "мята", "парта", "марка", "маска", "сумка",
    "банка", "ручка", "точка", "почта", "часть", "масть", "радость",
}

_WORD_RE = re.compile(r"[\u0400-\u04FF]+")


def _harmony_violations(text: str) -> List[Tuple[str, str, str]]:
    """Return ``(word, suffix, stem_vowel)`` for clear harmony violations.

    The check is deliberately conservative:

    * only words containing at least one Kazakh-specific letter are examined
      (this filters out most Russian/loanword roots);
    * the stem must end in an unambiguous vowel (glides ``у/и/я/ю`` are
      skipped because their harmony is context dependent);
    * all-caps abbreviations are ignored.
    """
    violations: List[Tuple[str, str, str]] = []
    for word in _WORD_RE.findall(text or ""):
        low = word.casefold()
        if word.isupper() or len(low) < 5:
            continue
        if not has_kazakh_specific(low):
            continue
        for suffix, required in _SUFFIXES:
            if not low.endswith(suffix) or len(low) - len(suffix) < 3:
                continue
            stem = low[:-len(suffix)]
            if stem in _HARMONY_BLACKLIST:
                continue
            if stem[-1] in GLIDE_FINAL_LETTERS:
                continue
            vowel, harmony = last_vowel(stem)
            if harmony and harmony != required:
                violations.append((word, suffix, vowel or ""))
                break
    return violations


def analyzer_suffix_harmony(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Detect possible vowel-harmony violations in Kazakh suffixes."""
    findings: List[Dict] = []
    for key, kk_val in kk.items():
        if key.startswith("@@"):
            continue
        violations = _harmony_violations(kk_val)
        if not violations:
            continue
        word, suffix, vowel = violations[0]
        expected = "передняя" if vowel in FRONT_VOWELS else "задняя"
        findings.append({
            "key": key,
            "category": "suffix_harmony",
            "original": ru.get(key) or "",
            "current": kk_val,
            "suggestion": None,
            "confidence": "low",
            "explanation": (
                f"В слове '{word}' суффикс '-{suffix}' может нарушать гармонию "
                f"гласных: последняя гласная основы '{vowel}' — {expected}. "
                "Проверьте вручную (консервативная эвристика)."
            ),
        })
    return findings


# ---------------------------------------------------------------------------
# 2. orthography – поиск латинских символов в казахском тексте
# ---------------------------------------------------------------------------
def analyzer_orthography(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Flag suspicious Latin tokens inside a Kazakh (Cyrillic) translation.

    Latin brand names, abbreviations, version strings and placeholders are
    legitimate and are filtered out.  For Latin-script targets (uz/en) this
    analyzer is not applicable and returns nothing.
    """
    if is_latin_locale(getattr(kk, "locale", None)):
        return []

    findings: List[Dict] = []
    for key, kk_val in kk.items():
        if key.startswith("@@"):
            continue
        src_val = ru.get(key) or ""
        # Source without Cyrillic (language codes, brand tokens, Latin UI
        # strings) is legitimate — Latin usage there is not an orthography error.
        if not CYRILLIC_RE.search(src_val):
            continue
        flagged = latin_tokens_to_flag(kk_val)
        if not flagged:
            continue
        findings.append({
            "key": key,
            "category": "orthography",
            "original": src_val,
            "current": kk_val,
            "suggestion": None,
            "confidence": "medium",
            "explanation": (
                "Текст содержит подозрительные латинские токены: "
                + ", ".join(sorted(set(flagged))[:6])
                + ". Проверьте, не остаток ли это английского текста."
            ),
        })
    return findings


# ---------------------------------------------------------------------------
# 3. plural_mismatch – проверка наличия множественного суффикса при {count}
# ---------------------------------------------------------------------------
_PLURAL_SUFFIXES = ["лар", "лер", "дар", "дер", "тар", "тер"]


def analyzer_plural_mismatch(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    locale = getattr(kk, "locale", None)
    if locale and str(locale).replace("_", "-").split("-")[0].lower() != "kk":
        return []

    findings: List[Dict] = []
    for key, ru_val in ru.items():
        if key.startswith("@@"):
            continue
        kk_val = kk.get(key)
        if not kk_val:
            continue
        if "{count}" in ru_val:
            if not any(suf in kk_val.casefold() for suf in _PLURAL_SUFFIXES):
                findings.append({
                    "key": key,
                    "category": "plural_mismatch",
                    "original": ru_val,
                    "current": kk_val,
                    "suggestion": None,
                    "confidence": "medium",
                    "explanation": "В русской строке есть плейсхолдер {count}, но в казахском переводе нет явного множественного суффикса.",
                })
    return findings


# ---------------------------------------------------------------------------
# 4. glossary_violation – проверка терминов из глоссария
# ---------------------------------------------------------------------------
def _get_current_glossary() -> Dict[str, Dict]:
    from ..glossary.service import glossary_service
    return glossary_service.get_all_terms()


def analyzer_glossary_violation(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Проверка, что термины из глоссария переводятся согласованно.

    Формат глоссария (пример):
    {
        "bonus": {"kk": "бонус", "variants": ["бонус", "бонусы"]},
        "coins": {"kk": "монеталар", "variants": ["монета", "монеталар"]}
    }
    """
    locale = getattr(kk, "locale", None)
    if locale and str(locale).replace("_", "-").split("-")[0].lower() != "kk":
        return []

    findings: List[Dict] = []
    current_glossary = _get_current_glossary()
    for ru_term, entry in current_glossary.items():
        expected = entry.get("kk")
        variants = set(entry.get("variants", []))
        for key, ru_val in ru.items():
            if key.startswith("@@"):
                continue
            if ru_term.casefold() in (ru_val or "").casefold():
                kk_val = kk.get(key, "")
                haystack = (kk_val or "").casefold()
                if expected.casefold() not in haystack and not any(
                    str(v).casefold() in haystack for v in variants
                ):
                    findings.append({
                        "key": key,
                        "category": "glossary_violation",
                        "original": ru_val,
                        "current": kk_val,
                        "suggestion": expected,
                        "confidence": "high",
                        "explanation": f"Термин '{ru_term}' должен переводиться как '{expected}'.",
                    })
    return findings


# ---------------------------------------------------------------------------
# 5. inconsistent_term – одинаковый русский текст переводится по-разному
# ---------------------------------------------------------------------------
# Source words with several unrelated meanings must not be treated as terms.
_HOMONYM_SOURCES = {
    "очки", "ключ", "коса", "лук", "мир", "свет", "язык", "нота",
    "парта", "соль", "ручка", "кран", "бал", "глава",
}


def analyzer_inconsistent_term(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Detect the same Russian source text translated differently.

    Term alignment without a bilingual lexicon is unreliable, so this
    analyzer deliberately focuses on a high-precision signal: keys whose
    *normalised source text is identical* but whose translations differ.
    When a clear majority translation exists only the minority keys are
    reported; on ties every key is reported with low confidence.

    This replaces the previous implementation which compared whole target
    sentences for every shared Russian word and produced ~4.6k false
    positives on the real ``intl_ru.arb``/``intl_kk.arb`` pair.
    """
    groups: Dict[str, List[str]] = defaultdict(list)
    for key, ru_val in ru.items():
        if key.startswith("@@"):
            continue
        normalised = normalize_for_compare(ru_val or "")
        if len(normalised) < 2 or not has_cyrillic(normalised):
            continue
        groups[normalised].append(key)

    findings: List[Dict] = []
    for source_norm, keys in groups.items():
        if len(keys) < 2:
            continue
        word_count = len(source_norm.split())
        if word_count == 1 and source_norm in _HOMONYM_SOURCES:
            continue

        variants: Dict[str, List[str]] = defaultdict(list)
        for key in keys:
            target_norm = normalize_for_compare(kk.get(key) or "")
            if target_norm:
                variants[target_norm].append(key)
        if len(variants) < 2:
            continue

        max_count = max(len(v) for v in variants.values())
        minority = {t: ks for t, ks in variants.items() if len(ks) < max_count}
        is_tie = not minority
        if is_tie:
            # No clear majority: report every variant, but as ambiguous.
            minority = variants

        variant_preview = " | ".join(sorted(variants.keys())[:3])
        if is_tie:
            confidence, prefix = "low", "Неоднозначно: "
        else:
            confidence = "medium" if word_count == 1 else "low"
            prefix = ""
        for _variant, variant_keys in minority.items():
            for key in variant_keys:
                findings.append({
                    "key": key,
                    "category": "inconsistent_term",
                    "original": ru.get(key) or "",
                    "current": kk.get(key) or "",
                    "suggestion": None,
                    "confidence": confidence,
                    "explanation": (
                        prefix
                        + "Одинаковый исходный текст переведён по-разному в разных ключах. "
                        f"Варианты: {variant_preview}"
                    ),
                })
    return findings


# ---------------------------------------------------------------------------
# 6. machine_translation_suspect – признаки частичного/машинного перевода
# ---------------------------------------------------------------------------
def analyzer_machine_translation_suspect(ru: ARBFile, kk: ARBFile) -> List[Dict]:
    """Detect partial translations and likely machine-translation artifacts.

    Signals are Russian function words and Russian-only endings inside a
    mixed Kazakh/Russian string, plus the Russian-only letters ``ё``/``ъ``.
    Pure Russian strings are handled by ``wrong_language``; exact copies are
    handled by ``untranslated``.
    """
    findings: List[Dict] = []
    if is_latin_locale(getattr(kk, "locale", None)):
        return findings

    for key, ru_val in ru.items():
        if key.startswith("@@"):
            continue
        target = kk.get(key) or ""
        if not target.strip():
            continue
        if ru_val and target == ru_val:
            continue
        if not has_cyrillic(target):
            continue

        stop, endings, strong = russian_signals(target)
        mixed_kazakh = has_kazakh_specific(target)
        signal_count = len(stop) + len(endings)

        if mixed_kazakh:
            # Kazakh text with Russian fragments → partial translation.
            if signal_count >= 1:
                details = sorted(set(stop + endings))[:5]
                findings.append({
                    "key": key,
                    "category": "machine_translation_suspect",
                    "original": ru_val,
                    "current": target,
                    "suggestion": None,
                    "confidence": "high" if signal_count >= 2 else "medium",
                    "explanation": (
                        "В казахском тексте остались русские слова/формы ("
                        + ", ".join(details) + "); возможен частичный или машинный перевод."
                    ),
                })
        else:
            # Pure Cyrillic text that differs from the source but still looks
            # Russian: likely a machine-translated/untouched fragment.
            if signal_count >= 2 or (signal_count >= 1 and strong):
                details = sorted(set(stop + endings))[:5]
                findings.append({
                    "key": key,
                    "category": "machine_translation_suspect",
                    "original": ru_val,
                    "current": target,
                    "suggestion": None,
                    "confidence": "medium",
                    "explanation": (
                        "Текст не совпадает с исходником, но содержит русские "
                        "конструкции (" + ", ".join(details) + "); возможен машинный перевод."
                    ),
                })
    return findings


# ---------------------------------------------------------------------------
# Aggregator for all Kazakh-specific checks
# ---------------------------------------------------------------------------
def run_kazakh_analyzers(ru_path: str, kk_path: str) -> List[Dict]:
    ru = ARBFile.load(ru_path)
    kk = ARBFile.load(kk_path)
    results: List[Dict] = []
    results.extend(analyzer_suffix_harmony(ru, kk))
    results.extend(analyzer_orthography(ru, kk))
    results.extend(analyzer_plural_mismatch(ru, kk))
    results.extend(analyzer_glossary_violation(ru, kk))
    results.extend(analyzer_inconsistent_term(ru, kk))
    results.extend(analyzer_machine_translation_suspect(ru, kk))
    return results
