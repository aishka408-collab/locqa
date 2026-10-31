#!/usr/bin/env python3
"""locqa — контроль качества локализации Flutter (.arb).

Пример:
  python3 locqa.py check --source data/intl_ru.arb --target data/intl_kk.arb \
      --glossary glossary.csv --out report.json

Рядом с report.json появится report.html (человекочитаемый отчёт).
Инструмент работает с любыми ARB: казахские проверки (аффиксы, буквы ә/ғ/қ…)
включаются только для локали kk, остальные — для всех языков.
"""

import argparse
import collections
import csv
import html
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- загрузка

def load_arb(path, duplicates=None):
    """Читает ARB. Возвращает (locale, {key: text}, {key: metadata}).
    Если передан список duplicates, в него попадут ключи, объявленные несколько раз."""
    path = Path(path)

    def hook(pairs):
        if duplicates is not None:
            seen = collections.defaultdict(list)
            for k, v in pairs:
                seen[k].append(v)
            duplicates.extend((k, v) for k, v in seen.items() if len(v) > 1 and not k.startswith("@"))
        return dict(pairs)

    data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=hook)
    texts, meta = {}, {}
    for k, v in data.items():
        if k.startswith("@@"):
            continue
        if k.startswith("@"):
            meta[k[1:]] = v
        elif isinstance(v, str):
            texts[k] = v
    locale = data.get("@@locale")
    if not locale:
        m = re.search(r"_([A-Za-z]{2,3})(?:_[A-Za-z]{2})?\.arb$", path.name)
        locale = m.group(1) if m else path.stem
    return locale.split("_")[0].lower(), texts, meta


def load_glossary(path):
    """CSV с колонками ru, kk, en, comment. В ru/kk можно перечислить варианты через «|».
    Необязательные колонки: kk_wrong — запрещённые варианты (через «|»);
    enforce=yes — термин обязателен (без него проверяется только kk_wrong);
    keep=yes — слово не переводится (имя, название механики, топоним, тех-термин):
    совпадение с русским для строк только из таких слов не считается ошибкой."""
    if not path:
        return []
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ru = [s.strip() for s in (row.get("ru") or "").split("|") if s.strip()]
            if not ru:
                continue
            rows.append({
                "ru": ru,
                "kk": [s.strip() for s in (row.get("kk") or "").split("|") if s.strip()],
                "kk_wrong": [s.strip() for s in (row.get("kk_wrong") or "").split("|") if s.strip()],
                "en": row.get("en", ""),
                "enforce": (row.get("enforce") or "").strip().lower() in {"1", "yes", "да", "true"},
                "keep": (row.get("keep") or "").strip().lower() in {"1", "yes", "да", "true"},
                "comment": row.get("comment", ""),
            })
    return rows

# ---------------------------------------------------------------- утилиты

WORD_RE = re.compile(r"[^\W\d_]+")
CYR = "А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІіЎўҲҳ"
CYR_RE = re.compile(f"[{CYR}]")
LAT_RE = re.compile(r"[A-Za-z]")
KK_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
UZ_CYR_LETTERS = set("ўҳЎҲ")
UZ_LAT_MARK_RE = re.compile(r"[oOgG][ʻ‘’'`]|[ʻ‘’]")
TAG_RE = re.compile(r"<[^<>]+>")


def strip_markup(text):
    """Убирает теги и плейсхолдеры, чтобы проверять только «живой» текст."""
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"\{[^{}]*\}", " ", text)
    text = re.sub(r"%\d*\$?[sdf@]", " ", text)
    return text


def words(text):
    return WORD_RE.findall(strip_markup(text))


def script_of(text):
    t = strip_markup(text)
    c, l = len(CYR_RE.findall(t)), len(LAT_RE.findall(t))
    if c + l == 0:
        return "none"
    return "cyr" if c >= l else "lat"


def placeholders(text):
    """Имена аргументов ICU ({name}, {count, plural, ...}) и printf (%s, %1$d)."""
    names = collections.Counter()
    depth, i = 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "'" and i + 1 < len(text) and text[i + 1] in "{}'":
            i += 2  # экранирование ICU
            continue
        if ch == "{":
            if depth == 0 or _in_plural_body(text, i):
                m = re.match(r"\{\s*(\w+)\s*[,}]", text[i:])
                if m:
                    names["{" + m.group(1) + "}"] += 1
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    for m in re.findall(r"%\d*\$?[sdf@]", text):
        names[m] += 1
    return names


def _in_plural_body(text, i):
    # аргумент внутри ветки plural ({count, plural, one{{name} …}}) тоже считаем
    return bool(re.match(r"\{\s*\w+\s*\}", text[i:]))


def balanced(text):
    depth = 0
    for ch in re.sub(r"'[{}]'", "", text):
        depth += ch == "{"
        depth -= ch == "}"
        if depth < 0:
            return False
    return depth == 0


PLURAL_RE = re.compile(r"\{\s*(\w+)\s*,\s*(plural|select|selectordinal)\s*,")


def icu_forms(text):
    """{arg: (kind, {форма})} для всех plural/select в строке."""
    out = {}
    for m in PLURAL_RE.finditer(text):
        i, depth, forms = m.end(), 0, set()
        start = i
        while i < len(text):
            ch = text[i]
            if ch == "{":
                if depth == 0:
                    sel = text[start:i].strip()
                    if sel:
                        forms.add(sel)
                depth += 1
            elif ch == "}":
                if depth == 0:
                    break
                depth -= 1
                if depth == 0:
                    start = i + 1
            i += 1
        out[m.group(1)] = (m.group(2), forms)
    return out


SHORT_UI_KEY_RE = re.compile(r"(button|btn|tab|title|header|label|caption|menu|hud|chip|badge)", re.I)


def looks_like_short_ui(key, source):
    return bool(SHORT_UI_KEY_RE.search(key)) or (len(source) <= 14 and "\n" not in source)


def finding(key, issue_type, severity, source, target, evidence, suggestion=None, confidence=0.8):
    return {
        "key": key,
        "issue_type": issue_type,
        "severity": severity,
        "source": source,
        "target": target,
        "evidence": evidence,
        "suggestion": suggestion,
        "confidence": round(confidence, 2),
        "reviewed_by_human": False,
    }

# ---------------------------------------------------------------- проверки

def is_fragment(key, source):
    """Кусок фразы, которую код склеивает из нескольких ключей."""
    return bool(re.search(r"(prefix|suffix|start|end|part\d|title\d|datetime)$", key, re.I)) \
        or source != source.strip() or len(source.strip()) <= 2


def check_untranslated(src, tgt, ctx):
    for key, s in src.items():
        t = tgt.get(key)
        if t is None:
            yield finding(key, "untranslated", "high", s, None,
                          "Ключа нет в целевом файле", confidence=1.0)
        elif not t.strip() and s.strip():
            if is_fragment(key, s):
                # кусок составной фразы (Prefix/Suffix): в казахском другой порядок слов,
                # пустое значение здесь обычно намеренное — по разметке носителя это ложные срабатывания
                continue
            else:
                yield finding(key, "untranslated", "high", s, t, "Перевод пустой", confidence=1.0)


# Слова, которые законно совпадают с русским (заимствования, бренды, сокращения).
LOANWORDS = {
    "бонус", "промокод", "профиль", "онлайн", "дилер", "аккаунт", "бизнес", "логин", "кейс",
    "мин", "сек", "макс", "графика", "кабинет", "вокзал", "фракция", "инкассатор", "баскет",
    "чат", "сервер", "админ", "банк", "такси", "гараж", "инвентарь", "статус", "рейтинг",
    "турнир", "лидер", "ок", "старт", "финиш", "лобби", "донат", "ивент", "квест", "топ",
}


RU_GRAMMAR_END_RE = re.compile(r"(ый|ий|ой|ая|яя|ое|ее|ые|ие|ть|ться|ешь|ите|ся|ов|ев|ами|ями|ях|ах|ого|ему|ому|ено|ано|ать|ить|еть)$")


def check_same_as_source(src, tgt, ctx):
    for key, s in src.items():
        t = tgt.get(key)
        if t is None or t != s:
            continue
        ws = [w.lower() for w in words(s)]
        if not ws:
            continue  # только цифры, знаки, плейсхолдеры
        if script_of(s) == "lat" and ctx["source_script"] == "cyr":
            continue  # бренды латиницей: Grand, BATTLE PASS
        if all(w in ctx.get("keep_words", ()) for w in ws if len(w) > 1):
            continue  # имена механик, персонажей, топонимы — из глоссария (keep=yes)
        unknown = [w for w in ws if w not in LOANWORDS and len(w) > 2]
        if not unknown:
            continue
        if len(ws) == 1:
            if not RU_GRAMMAR_END_RE.search(ws[0]):
                continue  # одиночное существительное — скорее всего заимствование (Арена, Турнир)
            sev, conf = "medium", 0.65
            ev = f"Слово «{s}» с русским окончанием совпадает с оригиналом — не переведено"
        else:
            sev, conf = ("high" if len(ws) >= 3 else "medium"), (0.9 if len(ws) >= 3 else 0.75)
            ev = f"Строка из {len(ws)} слов полностью совпадает с русским оригиналом"
        yield finding(key, "same_as_source", sev, s, t, ev, confidence=conf)


def check_placeholders(src, tgt, ctx):
    for key, s in src.items():
        t = tgt.get(key)
        if not t:
            continue
        ps, pt = placeholders(s), placeholders(t)
        # ICU-аргументы сравниваем как множества (в ветках plural их число законно разное),
        # printf-плейсхолдеры (%s) — с учётом количества
        ns = collections.Counter({k: 1 for k in ps if k.startswith("{")}) + \
            collections.Counter({k: v for k, v in ps.items() if k.startswith("%")})
        nt = collections.Counter({k: 1 for k in pt if k.startswith("{")}) + \
            collections.Counter({k: v for k, v in pt.items() if k.startswith("%")})
        if ns != nt:
            miss = sorted((ns - nt).elements())
            extra = sorted((nt - ns).elements())
            parts = []
            if miss:
                parts.append("пропущены " + ", ".join(miss))
            if extra:
                parts.append("лишние/изменённые " + ", ".join(extra))
            sugg, notes = t, []
            for ph in miss:
                name = ph.strip("{}")
                if ph.startswith("{") and re.search(rf"(?<![{{\w]){re.escape(name)}(?![\w}}])", t):
                    sugg = re.sub(rf"(?<![{{\w]){re.escape(name)}(?![\w}}])", ph, sugg, count=1)
                    notes.append(f"«{name}» потерял фигурные скобки")
            for bad in extra:
                close = [m for m in miss if m.startswith("{") and bad.startswith("{")
                         and (m.startswith(bad[:-1]) or bad.startswith(m[:-1]))]
                if close:
                    sugg = sugg.replace(bad, close[0])
                    notes.append(f"опечатка в имени: {bad} вместо {close[0]}")
            yield finding(key, "placeholder_mismatch", "high", s, t,
                          "Плейсхолдеры не совпадают с оригиналом: " + "; ".join(parts)
                          + (" (" + ", ".join(notes) + ")" if notes else ""),
                          sugg if sugg != t else None, confidence=0.97)


def check_plural(src, tgt, ctx):
    for key, s in src.items():
        t = tgt.get(key)
        if not t:
            continue
        fs, ft = icu_forms(s), icu_forms(t)
        if not fs and not ft and balanced(t):
            continue
        problems = []
        if not balanced(t):
            problems.append("несбалансированные фигурные скобки")
        for arg, (kind, forms) in fs.items():
            if arg not in ft:
                problems.append(f"в переводе нет {kind}-конструкции для {{{arg}}}")
                continue
            tforms = ft[arg][1]
            if "other" not in tforms:
                problems.append(f"{{{arg}}}: нет обязательной формы other")
            if kind == "select" and forms - tforms:
                problems.append(f"{{{arg}}}: нет вариантов select {sorted(forms - tforms)}")
        for arg, (kind, forms) in ft.items():
            if arg not in fs:
                problems.append(f"лишняя {kind}-конструкция {{{arg}}}")
            elif "other" not in forms and not any("other" in p for p in problems):
                problems.append(f"{{{arg}}}: нет обязательной формы other")
        if problems:
            yield finding(key, "plural_mismatch", "high", s, t, "; ".join(problems), confidence=0.95)


# Типичные «хвосты», которые раздувают короткие подписи
FILLER_RE = re.compile(r"\s*(режимін қосу және жалғастыру|туралы толық ақпарат алу үшін|"
                       r"бөліміне өту үшін басыңыз|үшін басыңыз|өту үшін басыңыз)\s*$", re.I)


def check_length(src, tgt, ctx):
    for key, s in src.items():
        t = tgt.get(key)
        if not t or len(s) > 25 or "\n" in s:
            continue
        if not looks_like_short_ui(key, s):
            continue
        if is_fragment(key, s):
            continue  # кусок составной фразы: длину надо оценивать по всей фразе, а не по части
        ls, lt = len(s), len(t)
        ratio = lt / ls
        is_button = bool(re.search(r"button|btn", key, re.I))
        filler = FILLER_RE.search(t)
        wordy = bool(filler) or ("басыңыз" in t.lower() and not re.search(r"нажм", s, re.I))
        if (ratio >= 3 and lt - ls >= 8) or (is_button and ratio >= 1.8 and lt - ls >= 6) or (wordy and ratio >= 1.6):
            sev = "high" if ratio >= 3 or wordy or is_button else "medium"
            hint = "ключ похож на кнопку" if is_button else "короткий элемент интерфейса"
            yield finding(key, "length_overflow", sev, s, t,
                          f"{lt} символов против {ls} в исходнике ({ratio:.1f}×), {hint}"
                          + (f"; лишняя фраза «{filler.group(0).strip()}»" if filler else
                             "; перевод похож на пояснение, а не на подпись" if wordy else ""),
                          (FILLER_RE.sub("", t).strip() or None) if filler else None,
                          confidence=min(0.95, 0.55 + (ratio - 2) * 0.15 + (0.15 if is_button or wordy else 0)))


# Аффиксы падежей/притяжательности, которые нельзя «жёстко» ставить после плейсхолдера.
KK_SUFFIXES = ("ға|ге|қа|ке|на|не|а|е|да|де|та|те|нда|нде|дан|ден|тан|тен|нан|нен|"
               "ның|нің|дың|дің|тың|тің|ны|ні|ды|ді|ты|ті|н|мен|бен|пен|ға|лар|лер|дар|дер|тар|тер|"
               "ы|і|сы|сі|ым|ім|ың|ің")
SUFFIX_RE = re.compile(r"(\{\w+\}|%\d*\$?[sd])(-?)(" + KK_SUFFIXES + r")(?![\w])")
CASE_NAMES = {
    "ға": "барыс", "ге": "барыс", "қа": "барыс", "ке": "барыс", "а": "барыс", "е": "барыс",
    "да": "жатыс", "де": "жатыс", "та": "жатыс", "те": "жатыс", "нда": "жатыс", "нде": "жатыс",
    "дан": "шығыс", "ден": "шығыс", "тан": "шығыс", "тен": "шығыс", "нан": "шығыс", "нен": "шығыс",
    "ның": "ілік", "нің": "ілік", "дың": "ілік", "дің": "ілік", "тың": "ілік", "тің": "ілік",
    "ны": "табыс", "ні": "табыс", "ды": "табыс", "ді": "табыс", "ты": "табыс", "ті": "табыс",
    "мен": "көмектес", "бен": "көмектес", "пен": "көмектес",
}
# Шаблоны переформулировки: падеж → нейтральная конструкция без аффикса.
REPHRASE = {
    "барыс": "{ph} үшін / «{ph}» деген",
    "жатыс": "«{ph}» ішінде / «{ph}» бойынша",
    "шығыс": "{ph} бастап / «{ph}» тарапынан",
    "ілік": "«{ph}» (атауы) — без притяжательного аффикса",
    "табыс": "«{ph}» деген (атауды тырнақшаға алу)",
    "көмектес": "{ph} арқылы / «{ph}» қатысуымен",
}


def check_suffix_harmony(src, tgt, ctx):
    if ctx["locale"] != "kk":
        return
    for key, t in tgt.items():
        for m in SUFFIX_RE.finditer(t):
            ph, suf = m.group(1), m.group(3)
            case = CASE_NAMES.get(suf, "аффикс")
            numeric = re.search(r"^\{(id|x|max|min|bet|sum)\}$|count|price|cost|level|place|amount|total|day|hour|minute|second|points|%d", ph, re.I)
            sev = "high" if not numeric else "medium"
            tmpl = REPHRASE.get(case)
            sugg = None
            if tmpl:
                sugg = t[:m.start()] + tmpl.split(" / ")[0].format(ph=ph) + t[m.end():]
            ev = (f"После {ph} жёстко стоит аффикс «-{suf}» ({case} септік). "
                  f"Подставляемое значение может требовать другой вариант по сингармонизму; "
                  f"переформулировать без аффикса или обработать в коде")
            yield finding(key, "suffix_harmony", sev, src.get(key, ""), t, ev, sugg,
                          confidence=0.9 if not numeric else 0.75)


KK_TO_RU = str.maketrans({"ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о", "ұ": "у",
                          "ү": "у", "һ": "х", "і": "и", "Ә": "А", "Ғ": "Г", "Қ": "К",
                          "Ң": "Н", "Ө": "О", "Ұ": "У", "Ү": "У", "Һ": "Х", "І": "И"})
HOMOGLYPHS = str.maketrans({"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
                            "y": "у", "i": "і", "A": "А", "C": "С", "E": "Е", "H": "Н",
                            "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х",
                            "B": "В", "I": "І"})


_C2L = dict(zip("абвгдежзийклмнопрстуфхцыэ", "abvgdejziyklmnoprstufxsie"))
_C2L.update({"ч": "ch", "ш": "sh", "ю": "yu", "я": "ya", "ў": "o‘", "қ": "q", "ғ": "g‘", "ҳ": "h", "ъ": "ʼ", "ь": ""})
CYR_TO_LAT = str.maketrans({**_C2L, **{k.upper(): v.upper() for k, v in _C2L.items()}})


def check_orthography(src, tgt, ctx):
    # 1) латинские буквы-двойники внутри кириллического слова (бөлімiне с латинской i)
    for key, t in tgt.items():
        mixed = sorted({w for w in words(t) if CYR_RE.search(w) and LAT_RE.search(w)}, key=t.index)
        if not mixed:
            continue
        lat = ctx["target_script"] == "lat"
        fixed_text = t
        for w in mixed:
            fixed_text = fixed_text.replace(w, w.translate(CYR_TO_LAT) if lat else w.translate(HOMOGLYPHS))
        what = "кириллические буквы в латинском слове" if lat else "латинские буквы-двойники в кириллическом слове"
        yield finding(key, "orthography", "high", src.get(key, ""), t,
                      f"{what}: " + ", ".join(f"«{w}»" for w in mixed), fixed_text, confidence=0.95)
    if ctx["locale"] != "kk":
        return
    # 2) казахская буква набрана русским аналогом: сравниваем с частотой написаний в файле
    freq = ctx["kk_word_freq"]
    by_skeleton = collections.defaultdict(collections.Counter)
    for w, n in freq.items():
        by_skeleton[w.translate(KK_TO_RU)][w] += n
    ru_vocab = ctx["source_vocab"]
    for key, t in tgt.items():
        if t == src.get(key):
            continue
        for w in set(words(t)):
            lw = w.lower()
            if len(lw) < 3 or not CYR_RE.search(lw):
                continue
            variants = by_skeleton[lw.translate(KK_TO_RU)]
            best, best_n = variants.most_common(1)[0]
            mine = variants[lw]
            kk_in_best = sum(ch in KK_LETTERS for ch in best)
            kk_in_mine = sum(ch in KK_LETTERS for ch in lw)
            if best != lw and best_n >= max(3, 3 * mine) and mine <= 2 and kk_in_best > kk_in_mine \
                    and (lw not in ru_vocab or best_n >= 5 * max(mine, 1)):
                fixed = best if w.islower() else (best.upper() if w.isupper() else best.capitalize())
                yield finding(key, "orthography", "medium", src.get(key, ""), t,
                              f"«{w}» встречается {mine} раз, а «{best}» — {best_n} раз: "
                              f"казахская буква, вероятно, набрана русским аналогом",
                              re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", fixed, t),
                              confidence=min(0.9, 0.6 + best_n / 50))


RU_ONLY_WORDS = {"и", "в", "для", "что", "это", "вы", "ваш", "ваша", "ваши", "вашего", "можно",
                 "нужно", "или", "как", "с", "по", "к", "у", "из", "за", "при", "чтобы",
                 "если", "был", "была", "будет", "необходим", "необходимо", "получить", "купить"}
RU_ENDINGS_RE = re.compile(r"\w{3,}(ться|ого|ому|ыми|ешь|ются|ется|ной|ного|ному)$")
CALQUE_PATTERNS = [
    (re.compile(r"болып табылады", re.I), "«болып табылады» — калька с «является»"),
    (re.compile(r"^Бұл - .{0,60}бар\b", re.I), "«Бұл - … бар …» — калька русской конструкции «Это …, в которой есть …»"),
    (re.compile(r"жүзеге асыр", re.I), "«жүзеге асыру» — канцелярская калька «осуществлять»"),
    (re.compile(r"\bарқылы\s+арқылы\b", re.I), "повтор «арқылы арқылы»"),
    (re.compile(r"(?<!\w)(\w+)\s+\1(?!\w)", re.I), "повтор слова подряд"),
]


URL_RE = re.compile(r"https?://\S+")


def texts_equal_brand(t):
    """«SUMMER RUSH», «CARBALL LEAGUE» — латиница капсом, вероятно, название ивента."""
    return t.isupper() or len(words(t)) == 1


def check_language(src, tgt, ctx):
    """wrong_language и machine_translation_suspect."""
    others = ctx["other_locales"]
    expected = ctx["target_script"]
    for key, t in tgt.items():
        s = src.get(key, "")
        if not t or not words(t):
            continue
        # точное совпадение с другим языковым файлом
        if URL_RE.fullmatch(t.strip()):
            continue
        hit = [loc for loc, texts in others.items()
               if texts.get(key) == t and t != s and script_of(t) != expected and script_of(s) != script_of(t)]
        if hit:
            strong = any(loc not in {"en"} for loc in hit) and len(words(t)) >= 1 and \
                not (set(hit) >= {"en"} and len(hit) > 1 and texts_equal_brand(t))
            yield finding(key, "wrong_language", "high" if strong else "medium", s, t,
                          f"Текст совпадает с файлом {'/'.join(hit)} и написан не той письменностью",
                          confidence=0.97 if strong else 0.6)
            continue
        sc = script_of(t)
        if sc != expected and sc != "none" and script_of(s) != sc:
            ws = words(t)
            lat_words = [w for w in ws if LAT_RE.search(w)]
            cyr_words = [w for w in ws if CYR_RE.search(w)]
            if len(lat_words) >= 2 and not cyr_words:
                lang = "узбекский" if UZ_LAT_MARK_RE.search(t) else "иностранный (латиница)"
                yield finding(key, "wrong_language", "high", s, t,
                              f"Строка написана латиницей ({lang}), а оригинал — кириллицей",
                              confidence=0.9)
                continue
        if ctx["locale"] == "kk" and any(ch in UZ_CYR_LETTERS for ch in t):
            yield finding(key, "wrong_language", "high", s, t,
                          "Встречаются узбекские буквы ў/ҳ, которых нет в казахском", confidence=0.9)
            continue
        if ctx["locale"] != "kk" or t == s:
            continue
        # русские слова внутри казахского текста
        ws = [w.lower() for w in words(t)]
        ru_words = [w for w in ws if (w in RU_ONLY_WORDS and len(ws) >= 2) or RU_ENDINGS_RE.match(w)]
        if ws and len(ru_words) / len(ws) >= 0.6 and len(ws) >= 2:
            yield finding(key, "wrong_language", "high", s, t,
                          f"Большая часть слов русские ({', '.join(ru_words[:5])}) — строка не переведена на казахский",
                          confidence=0.85)
            continue
        reasons = []
        if ru_words:
            reasons.append("русские слова/формы: " + ", ".join(sorted(set(ru_words))[:4]))
        for rx, why in CALQUE_PATTERNS:
            if rx.search(t):
                reasons.append(why)
        if reasons:
            yield finding(key, "machine_translation_suspect", "medium" if ru_words else "low", s, t,
                          "; ".join(reasons), confidence=0.7 if ru_words else 0.5)


def stem(term):
    t = term.lower()
    return t[:-1] if len(t) > 5 and t[-1] in "аяыиеоуйь" else t


def term_re(term, kk=False):
    """Термин в начале слова + короткое окончание (рус.) или любые аффиксы (каз.)."""
    tail = r"[^\W\d_]*" if kk else r"[^\W\d_]{0,4}"
    return re.compile(r"(?<![^\W\d_])" + re.escape(stem(term)) + tail + r"(?![^\W\d_])", re.I)


def check_glossary(src, tgt, ctx):
    gl = ctx["glossary"]
    if not gl or ctx["locale"] != "kk":
        return
    for key, s in src.items():
        t = tgt.get(key)
        if not t or t == s:
            continue
        sl, tl = strip_markup(s).lower(), strip_markup(t).lower()
        for row in gl:
            if not any(term_re(r).search(sl) for r in row["ru"]):
                continue
            wrong = [w for w in row["kk_wrong"] if term_re(w).search(tl)]
            ok = any(term_re(k, kk=True).search(tl) for k in row["kk"])
            if wrong or (row["enforce"] and row["kk"] and not ok):
                ev = f"В оригинале термин «{row['ru'][0]}», по глоссарию: «{' / '.join(row['kk'])}»"
                if wrong:
                    ev += f"; в переводе нежелательный вариант «{wrong[0]}»"
                sugg = None
                if wrong and row["kk"]:
                    sugg = re.sub(re.escape(stem(wrong[0])), stem(row["kk"][0]), t, flags=re.I)
                yield finding(key, "glossary_violation", "medium", s, t, ev, sugg,
                              confidence=0.85 if wrong else 0.6)


def check_inconsistent(src, tgt, ctx):
    """Одинаковая короткая русская строка переведена по-разному в разных ключах."""
    groups = collections.defaultdict(list)
    for key, s in src.items():
        t = tgt.get(key)
        if not t or t == s:
            continue
        norm = re.sub(r"\s+", " ", s.strip().lower().rstrip(":!.?"))
        if 1 <= len(norm.split()) <= 3 and words(norm):
            groups[norm].append((key, t))
    for norm, items in groups.items():
        variants = collections.Counter(re.sub(r"\s+", " ", t.strip().lower().rstrip(":!.?"))
                                       for _, t in items)
        if len(variants) < 2:
            continue
        best, best_n = variants.most_common(1)[0]
        if best_n < 3:
            continue  # нет явного большинства — пусть решает носитель
        for key, t in items:
            v = re.sub(r"\s+", " ", t.strip().lower().rstrip(":!.?"))
            if v != best:
                example = next(tt for kk_, tt in items if re.sub(r"\s+", " ", tt.strip().lower().rstrip(":!.?")) == best)
                yield finding(key, "inconsistent_term", "low", src[key], t,
                              f"«{src[key].strip()}» в {best_n} других ключах переведено как «{example}», здесь — иначе",
                              _match_case(example, t), confidence=0.6)


def _match_case(example, like):
    if like.isupper():
        return example.upper()
    return example


CHECKS = [check_untranslated, check_same_as_source, check_placeholders, check_plural,
          check_length, check_suffix_harmony, check_orthography, check_language,
          check_glossary, check_inconsistent]

# ---------------------------------------------------------------- запуск


CYR_LOCALES = {"ru", "kk", "uk", "be", "ky", "tg", "mn", "bg", "sr"}
LATIN_LOCALES = {"uz", "en", "es", "pt", "tr", "az", "de", "fr", "it", "pl", "id", "vi", "tk"}


def expected_script(locale, texts):
    if locale in CYR_LOCALES:
        return "cyr"
    if locale in LATIN_LOCALES:
        return "lat"
    votes = collections.Counter(script_of(t) for t in texts.values() if script_of(t) != "none")
    return votes.most_common(1)[0][0] if votes else "cyr"


def run(source_path, target_path, glossary_path=None, extra_paths=(), loose=False):
    """loose=True включает шумную эвристику «одна фраза — разные переводы» (по умолчанию выключена:
    на наших данных ~70% её находок оказались допустимыми синонимами)."""
    _src_locale, src, _meta = load_arb(source_path)
    dups = []
    locale, tgt, _ = load_arb(target_path, dups)
    others = {}
    for p in extra_paths:
        loc, texts, _ = load_arb(p)
        if loc != locale:
            others[loc] = texts
    kk_freq = collections.Counter(w.lower() for t in tgt.values() for w in words(t))
    ctx = {
        "locale": locale,
        "glossary": load_glossary(glossary_path),
        "other_locales": others,
        "kk_word_freq": kk_freq,
        "source_vocab": {w.lower() for s in src.values() for w in words(s)},
        "source_script": expected_script(_src_locale, src),
        "target_script": expected_script(locale, tgt),
    }
    ctx["keep_words"] = {w.lower() for row in ctx["glossary"] if row["keep"]
                         for term in row["ru"] for w in words(term)}
    findings = []
    for key, values in dups:
        if len(set(map(str, values))) > 1:
            findings.append(finding(
                key, "inconsistent_term", "medium", src.get(key, ""), " | ".join(map(str, values)),
                f"Ключ объявлен в файле {len(values)} раза с разными переводами; "
                f"Flutter возьмёт последний («{values[-1]}»)", None, 0.9))
    for check in CHECKS:
        if check is check_inconsistent and not loose:
            continue
        findings.extend(check(src, tgt, ctx))
    # одна запись на (ключ, тип) — меньше шума
    seen, unique = set(), []
    for f in findings:
        k = (f["key"], f["issue_type"])
        if k not in seen:
            seen.add(k)
            unique.append(f)
    order = {"high": 0, "medium": 1, "low": 2}
    unique.sort(key=lambda f: (order[f["severity"]], f["issue_type"], f["key"]))
    return locale, unique


def write_html(findings, path, title):
    counts = collections.Counter(f["issue_type"] for f in findings)
    sev = collections.Counter(f["severity"] for f in findings)
    rows = []
    for f in findings:
        rows.append(
            f'<tr data-type="{f["issue_type"]}" data-sev="{f["severity"]}">'
            f'<td><code>{html.escape(f["key"])}</code></td>'
            f'<td><span class="t">{f["issue_type"]}</span></td>'
            f'<td><span class="s {f["severity"]}">{f["severity"]}</span></td>'
            f'<td>{html.escape(f["source"] or "")}</td>'
            f'<td>{html.escape(f["target"] or "—")}</td>'
            f'<td>{html.escape(f["evidence"])}</td>'
            f'<td>{html.escape(f["suggestion"] or "")}</td>'
            f'<td>{f["confidence"]:.2f}</td></tr>')
    options = "".join(f'<option value="{t}">{t} ({n})</option>' for t, n in counts.most_common())
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font:14px system-ui,sans-serif;margin:24px;color:#1d1d1f;background:#fafafa}}
h1{{font-size:22px;margin:0 0 4px}} .sum{{color:#555;margin-bottom:16px}}
.bar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}}
select,input{{font:inherit;padding:6px 8px;border:1px solid #ccc;border-radius:6px}}
table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border-bottom:1px solid #eee;padding:8px;vertical-align:top;text-align:left;white-space:pre-wrap}}
th{{position:sticky;top:0;background:#f0f0f3}} code{{font-size:12px}}
.s{{padding:2px 8px;border-radius:10px;font-size:12px}}
.high{{background:#fde2e1;color:#a1150f}} .medium{{background:#fff1d6;color:#8a5a00}} .low{{background:#e7f0ff;color:#1d4fa0}}
.t{{font-size:12px;color:#444}}
</style>
<h1>{html.escape(title)}</h1>
<div class="sum">Находок: {len(findings)} · high {sev["high"]} · medium {sev["medium"]} · low {sev["low"]}</div>
<div class="bar"><select id="ft"><option value="">Все типы</option>{options}</select>
<select id="fs"><option value="">Любая важность</option><option>high</option><option>medium</option><option>low</option></select>
<input id="q" placeholder="Поиск по ключу или тексту"></div>
<table><thead><tr><th>Ключ</th><th>Тип</th><th>Важность</th><th>Оригинал</th><th>Перевод</th>
<th>Почему</th><th>Предложение</th><th>Увер.</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<script>
const ft=document.getElementById('ft'),fs=document.getElementById('fs'),q=document.getElementById('q');
function f(){{const t=ft.value,s=fs.value,x=q.value.toLowerCase();
document.querySelectorAll('tbody tr').forEach(r=>{{r.style.display=(!t||r.dataset.type===t)&&(!s||r.dataset.sev===s)&&(!x||r.textContent.toLowerCase().includes(x))?'':'none'}})}}
[ft,fs,q].forEach(e=>e.addEventListener('input',f));
</script></html>"""
    Path(path).write_text(page, encoding="utf-8")


# ---------------------------------------------------------------- проверка совместимости с flutter gen-l10n

DART_RESERVED = {"assert", "break", "case", "catch", "class", "const", "continue", "default", "do",
                 "else", "enum", "extends", "false", "final", "finally", "for", "if", "in", "is",
                 "new", "null", "rethrow", "return", "super", "switch", "this", "throw", "true",
                 "try", "var", "void", "while", "with"}
IDENT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def icu_errors(text):
    """Упрощённый разбор ICU по правилам Flutter gen-l10n (use-escaping: false)."""
    errors, depth, i = [], 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "{":
            m = re.match(r"\{\s*([^,{}]*?)\s*(?:,\s*(\w+)\s*(?:,|\}))?", text[i:])
            if depth == 0 or _in_plural_body(text, i) or m and m.group(2):
                name = m.group(1) if m else ""
                if m and name and not IDENT_RE.match(name):
                    errors.append(f"имя плейсхолдера «{name}» не является идентификатором Dart")
                if m and m.group(2) and m.group(2) not in {"plural", "select", "selectordinal"}:
                    errors.append(f"неизвестный тип «{m.group(2)}» в {{{name}, ...}}")
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                errors.append("лишняя закрывающая скобка }")
                depth = 0
        i += 1
    if depth > 0:
        errors.append("не закрыта фигурная скобка {")
    for arg, (kind, forms) in icu_forms(text).items():
        if "other" not in forms:
            errors.append(f"{{{arg}, {kind}}}: нет обязательной формы other")
        if kind == "plural":
            bad = [f for f in forms if not re.fullmatch(r"zero|one|two|few|many|other|=\d+", f)]
            if bad:
                errors.append(f"{{{arg}, plural}}: недопустимые формы {bad}")
    return errors


def validate_arb(path, template_path=None):
    """Возвращает (ошибки, предупреждения) — то, на чём упадёт или ругнётся gen-l10n."""
    errors, warnings, dups = [], [], []
    try:
        locale, texts, meta = load_arb(path, dups)
    except (ValueError, UnicodeDecodeError) as e:
        return [f"{path}: файл не читается как JSON: {e}"], []
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    for key, values in dups:
        warnings.append(f"{key}: ключ объявлен {len(values)} раза (останется последнее значение)")
    tmpl = load_arb(template_path)[1] if template_path else None
    for key, value in raw.items():
        if key.startswith("@"):
            if key != "@@locale" and not key.startswith("@@") and not isinstance(value, dict):
                errors.append(f"{key}: метаданные должны быть объектом")
            continue
        if not isinstance(value, str):
            errors.append(f"{key}: значение должно быть строкой")
            continue
        if not IDENT_RE.match(key) or key in DART_RESERVED:
            (warnings if tmpl and key in tmpl else errors).append(
                f"{key}: имя ключа не годится для метода Dart"
                + (" (такой же ключ есть в исходном файле — проблема не в переводе)" if tmpl and key in tmpl else ""))
        for e in icu_errors(value):
            errors.append(f"{key}: {e}")
        if tmpl is not None:
            if key not in tmpl:
                warnings.append(f"{key}: ключа нет в шаблоне (gen-l10n его проигнорирует)")
            else:
                ph_t = {k for k in placeholders(tmpl[key]) if k.startswith("{")}
                ph_v = {k for k in placeholders(value) if k.startswith("{")}
                if ph_v - ph_t:
                    errors.append(f"{key}: плейсхолдеры {sorted(ph_v - ph_t)} отсутствуют в шаблоне")
    if tmpl is not None:
        missing = [k for k in tmpl if k not in texts]
        if missing:
            warnings.append(f"нет перевода для {len(missing)} ключей шаблона (будет взят текст шаблона): "
                            + ", ".join(missing[:5]) + ("…" if len(missing) > 5 else ""))
    return errors, warnings

# ---------------------------------------------------------------- транслитерация (бонус)

# Казахский латинский алфавит, утверждённая версия 2021 г. (31 буква).
_KK_LAT = {
    "а": "a", "ә": "ä", "б": "b", "в": "v", "г": "g", "ғ": "ğ", "д": "d", "е": "e", "ё": "io", "ж": "j",
    "з": "z", "и": "i", "й": "i", "к": "k", "қ": "q", "л": "l", "м": "m", "н": "n", "ң": "ñ", "о": "o",
    "ө": "ö", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ұ": "ū", "ү": "ü", "ф": "f", "х": "h",
    "һ": "h", "ц": "ts", "ч": "ç", "ш": "ş", "щ": "şş", "ъ": "", "ы": "y", "і": "ı", "ь": "", "э": "e",
    "ю": "iu", "я": "ia",
}
_KK_LAT_UPPER = {"И": "İ", "Й": "İ"}
_PROTECTED = re.compile(r"(\{[^{}]*\}|<[^<>]+>|%\d*\$?[sdf@]|\\n)")


def kk_to_latin(text):
    """Кириллица → латиница; плейсхолдеры {…}, теги <…> и %s не трогаются."""
    out = []
    for part in _PROTECTED.split(text):
        if _PROTECTED.fullmatch(part or ""):
            out.append(part)
            continue
        res = []
        for i, ch in enumerate(part):
            low = ch.lower()
            if ch in _KK_LAT_UPPER:
                res.append(_KK_LAT_UPPER[ch])
            elif low in _KK_LAT:
                lat = _KK_LAT[low]
                if ch != low and lat:
                    prev = part[i - 1] if i > 0 else ""
                    nxt = part[i + 1] if i + 1 < len(part) else ""
                    caps_word = (prev.isalpha() and prev.isupper()) or (nxt.isalpha() and nxt.isupper())
                    lat = lat.upper() if caps_word else lat[0].upper() + lat[1:]
                res.append(lat)
            else:
                res.append(ch)
        out.append("".join(res))
    return "".join(out)


def transliterate_arb(source_path, target_path, out_path, ratio=1.15):
    """Пишет латинскую версию ARB (структура файла сохраняется) и возвращает строки,
    которые на латинице заметно длиннее кириллицы и могут не влезть в короткие элементы."""
    raw = Path(target_path).read_text(encoding="utf-8-sig")
    _, tgt, _ = load_arb(target_path)
    _, src, _ = load_arb(source_path)
    changes = {k: kk_to_latin(v) for k, v in tgt.items() if kk_to_latin(v) != v}
    text = write_preserving(raw, changes, {})
    text = re.sub(r'("@@locale"\s*:\s*)"kk"', r'\1"kk_Latn"', text, count=1)
    Path(out_path).write_text(text, encoding="utf-8")
    longer = []
    for key, lat in changes.items():
        cyr, s = tgt[key], src.get(key, "")
        if len(lat) > len(cyr) * ratio and len(lat) - len(cyr) >= 2:
            longer.append((key, cyr, lat, "длиннее кириллицы"))
        elif s and looks_like_short_ui(key, s) and len(s) <= 25 and len(lat) >= 2 * len(s) and len(cyr) < 2 * len(s):
            longer.append((key, cyr, lat, "на латинице стала вдвое длиннее оригинала"))
    return len(changes), longer

# ---------------------------------------------------------------- проверка носителем

REVIEW_FIELDS = ["key", "issue_type", "severity", "source", "target", "suggestion",
                 "evidence", "kk_final", "reviewer", "comment"]


def make_review_sheet(report_path, out_path, min_severity="low"):
    """CSV для носителя: заполнить kk_final (итоговый текст) и reviewer (имя)."""
    order = {"high": 0, "medium": 1, "low": 2}
    findings = json.loads(Path(report_path).read_text(encoding="utf-8"))
    rows = {}
    for f in findings:
        if order[f["severity"]] > order[min_severity] or f["target"] is None and f["issue_type"] != "untranslated":
            continue
        r = rows.setdefault(f["key"], {k: "" for k in REVIEW_FIELDS})
        r["key"] = f["key"]
        r["issue_type"] = ", ".join(filter(None, [r["issue_type"], f["issue_type"]]))
        r["severity"] = r["severity"] or f["severity"]
        r["source"], r["target"] = f["source"] or "", f["target"] or ""
        r["suggestion"] = r["suggestion"] or f["suggestion"] or ""
        r["evidence"] = " / ".join(filter(None, [r["evidence"], f["evidence"]]))
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS)
        w.writeheader()
        w.writerows(rows.values())
    return len(rows)


_STR = r'"(?:\\.|[^"\\])*"'
_KV_LINE = re.compile(r'^(?P<pre>\s*)(?P<k>' + _STR + r')(?P<sep>\s*:\s*)(?P<v>' + _STR + r')(?P<post>\s*,?\s*)$')


def write_preserving(target_text, changes, metas):
    """Вносит изменения в текст ARB, сохраняя всё остальное как было: порядок ключей,
    повторяющиеся «@@comment», пустые строки, отступы и переводы строк.
    changes: {key: новое значение}; metas: {key: dict метаданных для «@key»}."""
    nl = "\r\n" if "\r\n" in target_text else "\n"
    enc = lambda v: json.dumps(v, ensure_ascii=False)
    text = target_text
    # 1) существующие блоки «@key»: дополняем своими полями (остальные поля сохраняются)
    has_meta = set()
    for key, meta in metas.items():
        mk = re.search(r'(?m)^\s*' + re.escape(enc("@" + key)) + r'\s*:\s*\{', text)
        if mk:
            start = text.index("{", mk.start())
            old_meta, end = json.JSONDecoder().raw_decode(text, start)
            text = text[:start] + enc({**old_meta, **meta}) + text[end:]
            has_meta.add(key)
    # 2) значения: заменяем в строке «"key": "value",», запоминаем, где ключ встретился последний раз
    lines = text.split(nl)
    found, last_line_of = set(), {}
    for i, line in enumerate(lines):
        m = _KV_LINE.match(line)
        if not m:
            continue
        key = json.loads(m.group("k"))
        if key in changes:
            lines[i] = m.group("pre") + m.group("k") + m.group("sep") + enc(changes[key]) + m.group("post")
            found.add(key)
            last_line_of[key] = i
    # 3) новые «@key» — отдельной строкой сразу после значения
    inserts = collections.defaultdict(list)
    for key in metas:
        if key not in has_meta and key in last_line_of:
            inserts[last_line_of[key]].append(key)
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        for key in inserts.get(i, []):
            indent = re.match(r"\s*", line).group(0)
            meta_line = f'{indent}{enc("@" + key)}: {enc(metas[key])}'
            if out[-1].rstrip().endswith(","):
                out.append(meta_line + ",")
            else:  # значение было последней записью в файле
                out[-1] = out[-1].rstrip() + ","
                out.append(meta_line)
    text = nl.join(out)
    # 4) ключей не было в файле — дописываем перед закрывающей скобкой
    missing = [k for k in changes if k not in found]
    if missing:
        cut = text.rstrip().rfind("}")
        body, tail = text[:cut].rstrip(), text[cut:]
        if not body.endswith(("{", ",")):
            body += ","
        add = []
        for key in missing:
            add.append(f'  {enc(key)}: {enc(changes[key])}')
            if key in metas:
                add.append(f'  {enc("@" + key)}: {enc(metas[key])}')
        text = body + nl + ("," + nl).join(add) + nl + tail
    return text


def apply_review(target_path, review_path, out_path, allow_unreviewed=False):
    """Применяет итоговые тексты из таблицы проверки. Каждая изменённая строка получает метаданные
    x-reviewed_by_human / x-reviewer / x-original. Остальной файл (порядок, «@@comment»,
    форматирование) сохраняется байт в байт."""
    raw = Path(target_path).read_text(encoding="utf-8-sig")
    data = json.loads(raw)  # для повторяющихся ключей берётся последнее значение
    changed, rejected = 0, []
    changes, metas = {}, {}
    with open(review_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        final_col = next((c for c in ("kk_final", "uz_final", "final") if c in r), "kk_final")
        key, new = r.get("key", "").strip(), (r.get(final_col) or "").replace("\\n", "\n")
        reviewer = (r.get("reviewer") or "").strip()
        if not key or not new.strip():
            continue
        if not reviewer and not allow_unreviewed:
            rejected.append((key, "нет отметки reviewer"))
            continue
        old = data.get(key, "")
        if new == old:
            continue
        src_ph = placeholders(r.get("source") or "") if r.get("source") else None
        if not balanced(new) or (src_ph is not None and {k for k in src_ph if k.startswith("{")}
                                 != {k for k in placeholders(new) if k.startswith("{")}):
            rejected.append((key, "сломаны плейсхолдеры или скобки"))
            continue
        changes[key] = new
        meta = {"x-reviewed_by_human": bool(reviewer), "x-original": old}
        if reviewer:
            meta["x-reviewer"] = reviewer
        metas[key] = meta
        changed += 1
    text = write_preserving(raw, changes, metas)
    result = json.loads(text)  # страховка: результат обязан быть валидным JSON с нужными значениями
    for key, value in changes.items():
        assert result.get(key) == value, f"не удалось записать {key}"
    Path(out_path).write_text(text, encoding="utf-8")
    return changed, rejected

def mark_report_reviewed(report_path, review_path, html_path=None):
    """Ставит reviewed_by_human=true находкам, чьи ключи проверил человек (заполнен reviewer),
    и записывает итоговый текст носителя в suggestion. Возвращает число отмеченных находок."""
    with open(review_path, encoding="utf-8-sig", newline="") as fh:
        reviewed = {r["key"].strip(): r for r in csv.DictReader(fh)
                    if (r.get("reviewer") or "").strip() and (r.get("key") or "").strip()}
    findings = json.loads(Path(report_path).read_text(encoding="utf-8"))
    marked = 0
    for f in findings:
        r = reviewed.get(f["key"])
        if not r:
            continue
        f["reviewed_by_human"] = True
        final = (r.get("kk_final") or "").replace("\\n", "\n")
        if final.strip():
            f["suggestion"] = final
        marked += 1
    Path(report_path).write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    write_html(findings, html_path or str(Path(report_path).with_suffix(".html")),
               f"Отчёт locqa: {Path(report_path).stem}")
    return marked

# ---------------------------------------------------------------- LLM (необязательно)

LLM_PROMPT = """Ты редактор казахской локализации мобильной игры (GTA-подобная RPG).
Ниже пары: ключ, русский оригинал, казахский перевод. Найди ТОЛЬКО явные проблемы:
- machine_translation_suspect: калька с русского, неестественный порядок слов, русская грамматика;
- неверный смысл (перевод означает другое).
Нормальные переводы НЕ упоминай. Сленг игроков (варн, мут, фура, ГК, ивент, кейс) — норма.
Ответь JSON-массивом: [{"key": ..., "evidence": "кратко по-русски, что не так", "suggestion": "исправленный казахский текст"}].
Если проблем нет — []."""


def llm_review(src, tgt, model, batch=40, keys=None):
    """Отправляет строки в LLM пачками. Нужен ANTHROPIC_API_KEY и pip install anthropic.
    Возвращает (находки, стоимость_в_USD_оценочно)."""
    import anthropic  # noqa: импорт только при --llm
    client = anthropic.Anthropic()
    keys = [k for k in (keys or src) if tgt.get(k) and tgt[k] != src[k] and words(tgt[k])]
    out, tokens_in, tokens_out = [], 0, 0
    for i in range(0, len(keys), batch):
        chunk = keys[i:i + batch]
        lines = "\n".join(json.dumps({"key": k, "ru": src[k], "kk": tgt[k]}, ensure_ascii=False) for k in chunk)
        msg = client.messages.create(model=model, max_tokens=4000,
                                     messages=[{"role": "user", "content": LLM_PROMPT + "\n\n" + lines}])
        tokens_in += msg.usage.input_tokens
        tokens_out += msg.usage.output_tokens
        text = msg.content[0].text
        m = re.search(r"\[.*\]", text, re.S)
        for item in json.loads(m.group(0)) if m else []:
            k = item.get("key")
            if k in chunk:
                out.append(finding(k, "machine_translation_suspect", "medium", src[k], tgt[k],
                                   "LLM: " + item.get("evidence", ""), item.get("suggestion"), 0.6))
        print(f"  LLM: {min(i + batch, len(keys))}/{len(keys)}", file=sys.stderr)
    # цена за 1M токенов (вход, выход) — задаётся переменными окружения, по умолчанию 3 / 15 $
    price = (float(os.environ.get("LOCQA_LLM_PRICE_IN", 3)), float(os.environ.get("LOCQA_LLM_PRICE_OUT", 15)))
    cost = tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1]
    return out, {"model": model, "input_tokens": tokens_in, "output_tokens": tokens_out, "usd": round(cost, 3)}


# ---------------------------------------------------------------- оформление терминала

ISSUE_NAMES = {
    "untranslated": "нет перевода",
    "same_as_source": "осталось по-русски",
    "placeholder_mismatch": "сломан плейсхолдер",
    "plural_mismatch": "ошибка в plural/ICU",
    "glossary_violation": "не по глоссарию",
    "length_overflow": "слишком длинно",
    "machine_translation_suspect": "калька / неверный смысл",
    "inconsistent_term": "разнобой в терминах",
    "suffix_harmony": "аффикс после {…}",
    "orthography": "орфография",
    "wrong_language": "чужой язык",
}


class UI:
    """ANSI-цвета без зависимостей. Выключаются, если вывод не в терминал, задан NO_COLOR или --no-color."""

    def __init__(self, color=None):
        if color is None:
            color = (sys.stdout.isatty() and not os.environ.get("NO_COLOR")) or bool(os.environ.get("FORCE_COLOR"))
        self.on = color
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    def _c(self, code, text):
        return f"\033[{code}m{text}\033[0m" if self.on else str(text)

    def bold(self, t): return self._c("1", t)
    def dim(self, t): return self._c("2", t)
    def red(self, t): return self._c("31", t)
    def green(self, t): return self._c("32", t)
    def yellow(self, t): return self._c("33", t)
    def blue(self, t): return self._c("34", t)
    def cyan(self, t): return self._c("36", t)

    def sev(self, severity, text=None):
        text = severity if text is None else text
        return {"high": self.red, "medium": self.yellow, "low": self.blue}.get(severity, str)(text)

    def dot(self, severity):
        return self.sev(severity, "●")

    def header(self, title):
        print()
        print("  " + self.bold(self.cyan("locqa")) + self.dim("  ·  ") + self.bold(title))
        print("  " + self.dim("─" * 58))

    def row(self, label, value):
        print(f"  {self.dim(label.ljust(11))}{value}")

    def ok(self, text): print("  " + self.green("✓") + " " + text)
    def fail(self, text): print("  " + self.red("✗") + " " + text)
    def warn(self, text): print("  " + self.yellow("!") + " " + text)


def short(text, limit=64):
    text = str(text or "").replace("\n", " ⏎ ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def plural_ru(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def num(n):
    return f"{n:,}".replace(",", " ")


def print_check_summary(ui, findings, src_path, tgt_path, locale, n_src, n_tgt, glossary_path, n_terms,
                        json_path, html_path, top=5):
    ui.header("проверка локализации")
    ui.row("Исходник", f"{src_path} {ui.dim(f'({num(n_src)} строк)')}")
    ui.row("Перевод", f"{tgt_path} {ui.dim(f'· {locale} · {num(n_tgt)} строк')}")
    if glossary_path:
        ui.row("Глоссарий", f"{glossary_path} {ui.dim(f'({n_terms} терминов)')}")
    print()
    sev = collections.Counter(f["severity"] for f in findings)
    if not findings:
        ui.ok(ui.bold("Проблем не найдено"))
    else:
        print("  " + ui.bold(f"Найдено {num(len(findings))} {plural_ru(len(findings), 'проблема', 'проблемы', 'проблем')}") + "   "
              + "  ".join(f"{ui.dot(s_)} {s_} {ui.bold(sev[s_])}" for s_ in ("high", "medium", "low") if sev[s_]))
        print()
        counts = collections.Counter(f["issue_type"] for f in findings)
        width = 22
        peak = max(counts.values())
        for t, n in counts.most_common():
            bar = "█" * max(1, round(width * n / peak))
            name = ISSUE_NAMES.get(t, t)
            print(f"  {name:<24}{ui.cyan(bar):<{width + (9 if ui.on else 0)}} {ui.bold(str(n).rjust(4))}  {ui.dim(t)}")
        by_type = collections.defaultdict(list)
        for f in findings:
            if f["severity"] == "high":
                by_type[f["issue_type"]].append(f)
        highs, i = [], 0
        while len(highs) < top and any(len(v_) > i for v_ in by_type.values()):
            highs += [v_[i] for v_ in by_type.values() if len(v_) > i][: top - len(highs)]
            i += 1
        if highs:
            print()
            print("  " + ui.bold("Самые важные:"))
            for f in highs:
                print(f"  {ui.dot('high')} {ui.bold(f['key'])}  {ui.dim(ISSUE_NAMES.get(f['issue_type'], f['issue_type']))}")
                print(f"    {ui.dim('было ')} {short(f['target'] if f['target'] is not None else '— нет строки —')}")
                if f.get("suggestion"):
                    print(f"    {ui.green('стало')} {short(f['suggestion'])}")
            rest = sev["high"] - len(highs)
            if rest > 0:
                print(ui.dim(f"    … и ещё {rest} важных — смотрите HTML-отчёт"))
    print()
    ui.ok("Отчёт сохранён")
    ui.row("  JSON", json_path)
    ui.row("  HTML", f"{html_path}  {ui.dim('← откройте в браузере')}")
    print()


def main(argv=None):
    p = argparse.ArgumentParser(prog="locqa", description="Контроль качества ARB-локализаций")
    p.add_argument("--no-color", action="store_true", help="без цветов в терминале")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="проверить целевой ARB относительно исходного")
    c.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="без цветов")
    c.add_argument("--source", required=True, help="исходный ARB (например, intl_ru.arb)")
    c.add_argument("--target", required=True, help="проверяемый ARB (например, intl_kk.arb)")
    c.add_argument("--glossary", help="glossary.csv: ru,kk,en[,uz],comment[,kk_wrong,enforce]")
    c.add_argument("--out", default="report.json", help="куда сохранить JSON-отчёт")
    c.add_argument("--html", help="куда сохранить HTML (по умолчанию рядом с --out)")
    c.add_argument("--compare", nargs="*", default=None,
                   help="другие ARB для поиска «чужого языка» (по умолчанию все intl_*.arb рядом)")
    c.add_argument("--fail-on", choices=["high", "medium", "low", "never"], default="never",
                   help="код выхода 1, если есть находки этой важности (для CI)")
    c.add_argument("--llm", action="store_true", help="доп. проверка смысла через LLM (нужен ANTHROPIC_API_KEY)")
    c.add_argument("--llm-model", default=os.environ.get("LOCQA_LLM_MODEL"),
                   help="ID модели для --llm (или переменная окружения LOCQA_LLM_MODEL)")
    c.add_argument("--loose", action="store_true",
                   help="включить шумную проверку разных переводов одной короткой фразы")
    c.add_argument("--include", nargs="*", default=[],
                   help="JSON-файлы с готовыми находками (например, от ручной LLM-вычитки), добавляются в отчёт")

    r = sub.add_parser("review-sheet", help="сделать CSV для проверки носителем")
    r.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="без цветов")
    r.add_argument("--report", required=True)
    r.add_argument("--out", default="review.csv")
    r.add_argument("--min-severity", choices=["high", "medium", "low"], default="low")
    r.add_argument("--force", action="store_true", help="перезаписать существующую таблицу")

    ap = sub.add_parser("apply", help="применить проверенные правки и собрать *_fixed.arb")
    ap.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="без цветов")
    ap.add_argument("--target", required=True)
    ap.add_argument("--review", required=True, help="CSV с колонками key, kk_final, reviewer")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", help="report.json: отметить в нём проверенные человеком находки")
    ap.add_argument("--allow-unreviewed", action="store_true",
                    help="применять строки без reviewer (будут помечены x-reviewed_by_human=false)")
    tl = sub.add_parser("translit", help="казахский → латиница (алфавит 2021) с проверкой длины")
    tl.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="без цветов")
    tl.add_argument("--source", required=True, help="исходный ARB (для проверки коротких элементов)")
    tl.add_argument("--target", required=True, help="казахский ARB на кириллице")
    tl.add_argument("--out", required=True, help="куда записать латинскую версию")
    v = sub.add_parser("validate", help="проверить, что ARB соберётся через flutter gen-l10n")
    v.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="без цветов")
    v.add_argument("files", nargs="+")
    v.add_argument("--template", help="шаблонный ARB (template-arb-file из l10n.yaml)")
    a = p.parse_args(argv)

    ui = UI(color=False if getattr(a, "no_color", False) else None)
    try:  # «locqa check ... | head» не должен падать с BrokenPipeError
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass

    if a.cmd == "validate":
        ui.header("совместимость с flutter gen-l10n")
        if a.template:
            ui.row("Шаблон", a.template)
        print()
        bad = 0
        for f in a.files:
            errs, warns = validate_arb(f, a.template)
            if errs:
                ui.fail(f"{ui.bold(f)}  {ui.red(f'ошибок: {len(errs)}')}  {ui.dim(f'предупреждений: {len(warns)}')}")
            else:
                ui.ok(f"{ui.bold(f)}  {ui.green('соберётся')}  {ui.dim(f'предупреждений: {len(warns)}')}")
            for e in errs:
                print(f"      {ui.red('✗')} {e}")
            for w_ in warns[:10]:
                print(f"      {ui.yellow('!')} {ui.dim(w_)}")
            if len(warns) > 10:
                print(ui.dim(f"      … и ещё {len(warns) - 10} {plural_ru(len(warns) - 10, 'предупреждение', 'предупреждения', 'предупреждений')}"))
            bad += bool(errs)
        print()
        return 1 if bad else 0
    if a.cmd == "translit":
        ui.header("транслитерация в латиницу (алфавит 2021)")
        n, longer = transliterate_arb(a.source, a.target, a.out)
        ui.ok(f"Переведено в латиницу: {ui.bold(num(n))} {plural_ru(n, 'строка', 'строки', 'строк')}  →  {ui.bold(a.out)}")
        if longer:
            ui.warn(f"Стали заметно длиннее: {len(longer)} — проверьте, влезают ли в интерфейс")
            for key, cyr, lat, why in longer[:8]:
                print(f"      {ui.bold(key)}  {ui.dim(why)}")
                print(f"        {short(cyr, 50)}  →  {short(lat, 50)}")
            if len(longer) > 8:
                print(ui.dim(f"      … и ещё {len(longer) - 8}"))
        else:
            ui.ok("Длина не выросла заметно ни в одной строке")
        print()
        return 0
    if a.cmd == "review-sheet":
        ui.header("таблица для носителя")
        if Path(a.out).exists() and not a.force:
            ui.warn(f"{ui.bold(a.out)} уже существует — в нём могут быть правки носителя.")
            print(ui.dim("    Укажите другой --out или добавьте --force, чтобы перезаписать."))
            print()
            return 1
        n = make_review_sheet(a.report, a.out, a.min_severity)
        ui.ok(f"Создана {ui.bold(a.out)} — {n} строк")
        print(ui.dim("    Носитель заполняет kk_final (итоговый текст) и reviewer (своё имя)."))
        print()
        return 0
    if a.cmd == "apply":
        ui.header("сборка исправленного файла")
        n, rejected = apply_review(a.target, a.review, a.out, a.allow_unreviewed)
        ui.ok(f"Изменено строк: {ui.bold(n)}  →  {ui.bold(a.out)}")
        if a.allow_unreviewed:
            ui.warn("режим --allow-unreviewed: непроверенные строки помечены x-reviewed_by_human=false")
        not_reviewed = [k for k, why in rejected if why == "нет отметки reviewer"]
        if not_reviewed:
            ui.warn(f"ещё не проверено носителем: {len(not_reviewed)} строк {ui.dim('(в файл не попали)')}")
        for key, why in rejected:
            if why != "нет отметки reviewer":
                ui.fail(f"{ui.bold(key)}: {why}")
        if a.report:
            m = mark_report_reviewed(a.report, a.review)
            ui.ok(f"В отчёте {ui.bold(a.report)} отмечено проверенными: {m} находок {ui.dim('(HTML обновлён)')}")
        print()
        return 0

    compare = a.compare
    if compare is None:
        folder = Path(a.target).parent
        compare = [x for x in folder.glob("*.arb")
                   if x.resolve() not in {Path(a.source).resolve(), Path(a.target).resolve()}]
    locale, findings = run(a.source, a.target, a.glossary, compare, a.loose)
    if a.llm:
        if not a.llm_model:
            p.error("для --llm укажите модель: --llm-model <ID> или LOCQA_LLM_MODEL")
        _, src, _ = load_arb(a.source)
        _, tgt, _ = load_arb(a.target)
        extra, cost = llm_review(src, tgt, a.llm_model)
        have = {(f["key"], f["issue_type"]) for f in findings}
        findings += [f for f in extra if (f["key"], f["issue_type"]) not in have]
        Path(a.out).with_suffix(".llm_cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")
        ui.row("LLM", f"{cost['model']}: {num(cost['input_tokens'])} + {num(cost['output_tokens'])} токенов ≈ ${cost['usd']}")
    for extra_path in a.include:
        extra = json.loads(Path(extra_path).read_text(encoding="utf-8"))
        have = {(f["key"], f["issue_type"]) for f in findings}
        findings += [f for f in extra if (f["key"], f["issue_type"]) not in have]
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["issue_type"], f["key"]))

    Path(a.out).write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = a.html or str(Path(a.out).with_suffix(".html"))
    write_html(findings, html_path, f"Отчёт locqa: {Path(a.target).name}")

    n_src = len(load_arb(a.source)[1])
    n_tgt = len(load_arb(a.target)[1])
    n_terms = len(load_glossary(a.glossary)) if a.glossary else 0
    print_check_summary(ui, findings, a.source, a.target, locale, n_src, n_tgt, a.glossary, n_terms,
                        a.out, html_path)

    if a.fail_on != "never":
        levels = ["high", "medium", "low"][: ["high", "medium", "low"].index(a.fail_on) + 1]
        if any(f["severity"] in levels for f in findings):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
