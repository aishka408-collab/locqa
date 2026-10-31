"""
Unified Analyzer Orchestrator for Qazaq Loc QA.
Runs all 11 error analyzers with isolated exception handling, logging,
and deduplication.
"""
import logging
from typing import List, Dict, Any, Optional, Tuple, Callable
from pathlib import Path

from ..utils.arb_parser import ARBFile
from ..analyzers.base_analyzers import (
    analyzer_untranslated,
    analyzer_same_as_source,
    analyzer_placeholder_mismatch,
    analyzer_wrong_language,
    analyzer_length_overflow,
)
from ..analyzers.kazakh_analyzers import (
    analyzer_suffix_harmony,
    analyzer_orthography,
    analyzer_plural_mismatch,
    analyzer_glossary_violation,
    analyzer_inconsistent_term,
    analyzer_machine_translation_suspect,
)

logger = logging.getLogger("qazaq_loc_qa.analysis")

# Registered 11 analyzers
ANALYZERS: List[Tuple[str, Callable[[ARBFile, ARBFile], List[Dict[str, Any]]]]] = [
    ("untranslated", analyzer_untranslated),
    ("same_as_source", analyzer_same_as_source),
    ("placeholder_mismatch", analyzer_placeholder_mismatch),
    ("wrong_language", analyzer_wrong_language),
    ("length_overflow", analyzer_length_overflow),
    ("suffix_harmony", analyzer_suffix_harmony),
    ("orthography", analyzer_orthography),
    ("plural_mismatch", analyzer_plural_mismatch),
    ("glossary_violation", analyzer_glossary_violation),
    ("inconsistent_term", analyzer_inconsistent_term),
    ("machine_translation_suspect", analyzer_machine_translation_suspect),
]


def run_all_analyzers(ru: ARBFile, kk: ARBFile) -> List[Dict[str, Any]]:
    """Run all 11 registered analyzers against ru (source) and kk (target).
    
    Requirements:
    1. Unified mechanism for all 11 analyzers.
    2. Exception in one analyzer must not abort others (logged).
    3. Standardized error fields: key, category, source, current, suggestion, explanation, confidence.
    4. Deduplication of (key, category) pairs.
    """
    raw_findings: List[Dict[str, Any]] = []

    for name, analyzer_fn in ANALYZERS:
        try:
            findings = analyzer_fn(ru, kk)
            if findings:
                raw_findings.extend(findings)
        except Exception as exc:
            logger.error("Analyzer '%s' raised an exception: %s", name, exc, exc_info=True)

    # Standardize and deduplicate
    seen_keys: set = set()
    standardized: List[Dict[str, Any]] = []

    for item in raw_findings:
        key = item.get("key", "")
        category = item.get("category", "")
        dedup_tuple = (key, category)
        if dedup_tuple in seen_keys:
            continue
        seen_keys.add(dedup_tuple)

        source_text = item.get("original") or item.get("source") or ru.get(key) or ""
        current_text = item.get("current") or item.get("target_text") or kk.get(key) or ""
        explanation = item.get("explanation") or item.get("description") or item.get("reason") or ""
        suggestion = item.get("suggestion")
        confidence = item.get("confidence") or "medium"
        severity = item.get("severity")
        if not severity:
            severity = "high" if confidence == "high" else ("medium" if confidence == "medium" else "low")

        standardized.append({
            "key": key,
            "category": category,
            "error_type": category,
            "source": source_text,
            "original": source_text,
            "current": current_text,
            "suggestion": suggestion,
            "explanation": explanation,
            "description": explanation,
            "confidence": confidence,
            "severity": severity,
        })

    return standardized
