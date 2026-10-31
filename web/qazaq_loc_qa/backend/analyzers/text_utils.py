"""Shared text helpers for the Qazaq Loc QA analyzers.

The functions here are intentionally small and dependency-free.  They are
used by both the base (language-agnostic) and the Kazakh-specific analyzers so
that script detection, Russian-signal detection and comparison normalisation
behave identically everywhere.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

# Kazakh-specific Cyrillic letters (not used by Russian).  Note: ``е``, ``и``,
# ``у`` etc. are *shared* and must never be treated as Kazakh-specific markers.
KAZAKH_SPECIFIC_LETTERS = set("әғқңөұүһі")

CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
LATIN_RE = re.compile(r"[A-Za-z]")
# A Cyrillic token that is not glued to a Latin word or a hyphenated suffix
# (e.g. ``Season Pass-ты`` must not yield the Russian stop word ``ты``).
CYRILLIC_TOKEN_RE = re.compile(r"(?<![\w-])([\u0400-\u04FF]+)(?![\w-])")
PLACEHOLDER_RE = re.compile(r"\{\w+\}|\{[^}]*\}|<[^>]+>|%\d*[sdif]|%\{[^}]+\}")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

# Russian function words that rarely appear as standalone words in Kazakh.
RUSSIAN_STOPWORDS = {
    "это", "как", "для", "или", "что", "чтобы", "если", "когда", "где",
    "кто", "чем", "без", "при", "над", "под", "про", "через", "также",
    "уже", "еще", "всё", "все", "этот", "эта", "эти", "ваш", "ваша",
    "ваше", "вы", "мы", "они", "он", "она", "можно", "нельзя", "будет",
    "есть", "пожалуйста", "нажмите", "выберите", "купить", "продать",
    "получить", "уровень", "игра", "игрок", "друг", "деньги", "время",
    "сегодня", "завтра", "вчера", "очень", "только", "нужно", "надо",
    "самый", "более", "менее", "него", "нему", "них", "нами", "вами",
    "свой", "своя", "свои", "который", "которая", "которые", "этого",
}

# Russian adjectival / verbal / nominal endings that are practically absent
# from native Kazakh words.
RUSSIAN_ENDINGS = (
    "ый", "ий", "ая", "ое", "ые", "ого", "его", "ому", "ему", "ами",
    "ями", "ться", "ешь", "ишь", "ать", "ять", "ует", "ится", "ение",
    "ание", "ость", "ство", "ской", "ская", "ское", "ую", "юю", "ять",
    "ишь", "ите", "ился", "илась", "ились",
)

# Latin tokens that are legitimate inside a Kazakh UI (brands, platforms,
# formats, abbreviations).  Matching is case-insensitive.
LATIN_WHITELIST = {
    "vip", "vvip", "gc", "id", "sms", "mms", "qr", "apk", "faq", "pvp",
    "pve", "dlc", "xp", "hp", "mp", "ok", "tv", "hd", "uhd", "gb", "mb",
    "kb", "fps", "ping", "cpu", "gpu", "ram", "rom", "usb", "pdf", "csv",
    "json", "arb", "excel", "html", "url", "api", "sdk", "ui", "ux",
    "grand", "coins", "coin", "season", "pass", "ticket", "premium",
    "gold", "silver", "bronze", "google", "apple", "facebook", "vk",
    "youtube", "telegram", "whatsapp", "tiktok", "instagram", "ios",
    "android", "windows", "mac", "pc", "email", "online", "offline",
    "pro", "plus", "bonus", "battle", "pass", "drag", "racing", "twix",
    "chupa", "chups", "sber", "rustore", "tesla", "grand", "mobile",
    "polimat", "noclip", "season", "black", "friday", "promo", "code",
    "discord", "arena", "list", "twitter", "summer", "rush", "chicken",
    "battlepass", "cargo", "sale", "getapps", "spain", "jackpot", "role",
    "play", "ferrari", "silverstar", "bigbon", "qi", "lvl", "steam",
    "twitch", "spotify", "netflix", "telegram", "whatsapp", "viber",
}

FRONT_VOWELS = set("әеөүіиэё")
BACK_VOWELS = set("аоұыуяю")
# Final glide letters are ambiguous for harmony (``жеткізуге``, ``модерацияда``).
GLIDE_FINAL_LETTERS = set("уияю")


def has_kazakh_specific(text: str) -> bool:
    return any(ch in KAZAKH_SPECIFIC_LETTERS for ch in (text or "").casefold())


def has_cyrillic(text: str) -> bool:
    return bool(CYRILLIC_RE.search(text or ""))


def has_latin(text: str) -> bool:
    return bool(LATIN_RE.search(text or ""))


def is_latin_locale(locale: Optional[str]) -> bool:
    """Return True for languages normally written in Latin script."""
    if not locale:
        return False
    primary = str(locale).strip().lower().replace("_", "-").split("-")[0]
    return primary in {"uz", "en"}


def cyrillic_tokens(text: str) -> List[str]:
    return CYRILLIC_TOKEN_RE.findall(text or "")


def strip_placeholders(text: str) -> str:
    text = URL_RE.sub(" ", text or "")
    text = EMAIL_RE.sub(" ", text)
    return PLACEHOLDER_RE.sub(" ", text)


def meaningful_length(text: str) -> int:
    """Length of visible characters, excluding placeholders, tags and spaces."""
    cleaned = strip_placeholders(text)
    cleaned = re.sub(r"[^\w\u0400-\u04FF]", "", cleaned)
    return len(cleaned)


def normalize_for_compare(text: str) -> str:
    """Normalise a string for duplicate/consistency comparison.

    Placeholders, tags, URLs and punctuation are removed, dashes are treated as
    spaces and the result is case-folded.  This makes ``"Қолма-қол ақша"`` and
    ``"қолма қол ақша"`` compare equal without hiding genuine word changes.
    """
    cleaned = strip_placeholders(text)
    cleaned = re.sub(r"[^\w\s\u0400-\u04FF]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.casefold()


def _looks_like_proper_name(token: str) -> bool:
    """Heuristic: capitalised surnames/adjectives that must not trigger endings."""
    if not token or not token[0].isupper():
        return False
    low = token.casefold()
    return low.endswith(("ский", "цкий", "ская", "ское", "ских", "цких"))


def russian_signals(text: str) -> Tuple[List[str], List[str], bool]:
    """Return Russian stop words, Russian-ending tokens and a strong-script flag.

    ``strong`` is True when the text contains ``ё`` or ``ъ`` – letters that do
    not exist in the Kazakh alphabet.
    """
    tokens = cyrillic_tokens(text)
    stop = [t for t in tokens if t.casefold() in RUSSIAN_STOPWORDS]
    endings = [
        t for t in tokens
        if len(t) >= 5
        and not _looks_like_proper_name(t)
        and t.casefold().endswith(RUSSIAN_ENDINGS)
    ]
    strong = any(ch in (text or "") for ch in "ёъ")
    return stop, endings, strong


def is_abbreviation(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    letters_only = re.sub(r"[^\w\u0400-\u04FF]", "", stripped)
    return bool(letters_only) and letters_only.isupper() and len(letters_only) <= 8


def latin_tokens_to_flag(text: str) -> List[str]:
    """Return Latin tokens that are suspicious inside a Cyrillic (Kazakh) string.

    Brand names, platform names, abbreviations and tokens with digits are
    considered legitimate and are not returned.
    """
    cleaned = URL_RE.sub(" ", text or "")
    cleaned = EMAIL_RE.sub(" ", cleaned)
    cleaned = PLACEHOLDER_RE.sub(" ", cleaned)
    flagged: List[str] = []
    for token in LATIN_TOKEN_RE.findall(cleaned):
        low = token.casefold()
        if len(token) < 3:
            # Two-letter codes (kk/en/no/qi) are language-neutral.
            continue
        if token.isupper():
            # Abbreviations and promo codes.
            continue
        if any(ch.isdigit() for ch in token):
            continue
        if re.search(r"(.)\1{3,}", token):
            # Usernames / keyboard mash (e.g. "wohooooooo").
            continue
        if low in LATIN_WHITELIST or "grand" in low:
            continue
        flagged.append(token)
    return flagged


def last_vowel(word: str) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(vowel, harmony)`` where harmony is ``"front"`` or ``"back"``."""
    for ch in reversed(word or ""):
        low = ch.casefold()
        if low in FRONT_VOWELS:
            return low, "front"
        if low in BACK_VOWELS:
            return low, "back"
    return None, None
