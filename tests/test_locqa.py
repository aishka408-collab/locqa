"""Тесты на маленьких выдуманных ARB-файлах. Запуск: python3 -m pytest tests"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import locqa  # noqa: E402


def arb(tmp_path, name, locale, data):
    p = tmp_path / name
    p.write_text(json.dumps({"@@locale": locale, **data}, ensure_ascii=False), encoding="utf-8")
    return p


def types(findings, key):
    return {f["issue_type"] for f in findings if f["key"] == key}


def run(tmp_path, ru, kk, uz=None, glossary=None, loose=True):
    src = arb(tmp_path, "intl_ru.arb", "ru", ru)
    tgt = arb(tmp_path, "intl_kk.arb", "kk", kk)
    extra = [arb(tmp_path, "intl_uz.arb", "uz", uz)] if uz else []
    g = None
    if glossary:
        g = tmp_path / "g.csv"
        g.write_text(glossary, encoding="utf-8")
    return locqa.run(src, tgt, g, extra, loose)[1]


def test_untranslated(tmp_path):
    f = run(tmp_path, {"a": "Привет", "b": "Пока"}, {"a": "Сәлем"})
    assert types(f, "b") == {"untranslated"}


def test_same_as_source_ignores_brands_and_loanwords(tmp_path):
    f = run(tmp_path,
            {"brand": "Grand Mobile", "loan": "Бонус", "num": "100", "bad": "Купить новый дом сейчас"},
            {"brand": "Grand Mobile", "loan": "Бонус", "num": "100", "bad": "Купить новый дом сейчас"})
    assert not types(f, "brand") and not types(f, "loan") and not types(f, "num")
    assert "same_as_source" in types(f, "bad")


def test_placeholders(tmp_path):
    f = run(tmp_path, {"a": "Привет, {name}!", "b": "%s монет"}, {"a": "Сәлем, {nam}!", "b": "монет"})
    assert "placeholder_mismatch" in types(f, "a")
    assert "placeholder_mismatch" in types(f, "b")


def test_plural(tmp_path):
    ru = {"p": "{count, plural, one{{count} монета} few{{count} монеты} other{{count} монет}}"}
    ok = {"p": "{count, plural, other{{count} тиын}}"}
    bad = {"p": "{count, plural, one{{count} тиын}}"}
    assert not types(run(tmp_path, ru, ok), "p")
    assert "plural_mismatch" in types(run(tmp_path, ru, bad), "p")


def test_broken_braces(tmp_path):
    f = run(tmp_path, {"p": "{count, plural, other{монет}}"}, {"p": "{count, plural, other{монет}"})
    assert "plural_mismatch" in types(f, "p")


def test_length_overflow(tmp_path):
    f = run(tmp_path, {"garage_rent_button": "Арендовать"},
            {"garage_rent_button": "Жалға алу үшін басыңыз"})
    assert "length_overflow" in types(f, "garage_rent_button")


def test_suffix_harmony(tmp_path):
    f = run(tmp_path, {"a": "Отправить {name}", "b": "Игрок {name}"},
            {"a": "{name}-ға жіберу", "b": "Ойыншы {name}"})
    assert "suffix_harmony" in types(f, "a")
    assert not types(f, "b")
    sugg = next(x["suggestion"] for x in f if x["key"] == "a")
    assert "{name}" in sugg and "-ға" not in sugg


def test_homoglyph(tmp_path):
    f = run(tmp_path, {"a": "раздел"}, {"a": "бөлімiне"})  # латинская i
    assert "orthography" in types(f, "a")


def test_russian_letter_instead_of_kazakh(tmp_path):
    ru = {f"k{i}": f"Купить {i}" for i in range(5)}
    kk = {f"k{i}": "Сатып алу қазір" for i in range(5)}
    ru["bad"], kk["bad"] = "Купить сейчас", "Сатып алу казір"
    f = run(tmp_path, ru, kk)
    assert "orthography" in types(f, "bad")


def test_wrong_language_from_uz(tmp_path):
    f = run(tmp_path, {"a": "Ошибка авторизации", "b": "Дом"},
            {"a": "Avtorizatsiya xatosi", "b": "Үй"},
            uz={"a": "Avtorizatsiya xatosi", "b": "Uy"})
    assert "wrong_language" in types(f, "a")
    assert not types(f, "b")


def test_glossary(tmp_path):
    g = "ru,kk,en,comment,kk_wrong\nсемья,отбасы,Family,,жанұя\n"
    f = run(tmp_path, {"a": "Моя семья", "b": "Семья"}, {"a": "Менің жанұям", "b": "Отбасы"}, glossary=g)
    assert "glossary_violation" in types(f, "a")
    assert not types(f, "b")


def test_inconsistent(tmp_path):
    ru = {"a": "Все призы", "b": "Все призы", "d": "Все призы", "c": "Все призы"}
    kk = {"a": "Барлық жүлделер", "b": "Барлық жүлделер", "d": "Барлық жүлделер", "c": "Барлық сыйлықтар"}
    f = run(tmp_path, ru, kk)
    assert "inconsistent_term" in types(f, "c")


def test_report_schema(tmp_path):
    f = run(tmp_path, {"a": "Привет"}, {})
    assert set(f[0]) == {"key", "issue_type", "severity", "source", "target", "evidence",
                         "suggestion", "confidence", "reviewed_by_human"}


def test_works_for_uzbek_latin(tmp_path):
    src = arb(tmp_path, "intl_ru.arb", "ru", {"a": "Дом", "b": "Купить сейчас машину"})
    tgt = arb(tmp_path, "intl_uz.arb", "uz", {"a": "Uy", "b": "Купить сейчас машину"})
    f = locqa.run(src, tgt)[1]
    assert "same_as_source" in types(f, "b")


def test_review_and_apply(tmp_path):
    import csv
    src = arb(tmp_path, "intl_ru.arb", "ru", {"a": "Привет, {name}!", "b": "Купить"})
    tgt = arb(tmp_path, "intl_kk.arb", "kk", {"a": "Сәлем, {nam}!", "b": "Сатып алу"})
    findings = locqa.run(src, tgt)[1]
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
    sheet = tmp_path / "review.csv"
    assert locqa.make_review_sheet(rep, sheet) == 1
    rows = list(csv.DictReader(open(sheet, encoding="utf-8-sig")))
    rows[0]["kk_final"], rows[0]["reviewer"] = "Сәлем, {name}!", "Айша"
    with open(sheet, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    out = tmp_path / "fixed.arb"
    n, rejected = locqa.apply_review(tgt, sheet, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert n == 1 and not rejected
    assert data["a"] == "Сәлем, {name}!" and data["@a"]["x-reviewed_by_human"] is True


def test_apply_rejects_broken_placeholders(tmp_path):
    tgt = arb(tmp_path, "intl_kk.arb", "kk", {"a": "Сәлем, {name}!"})
    sheet = tmp_path / "review.csv"
    sheet.write_text("key,source,kk_final,reviewer\na,\"Привет, {name}!\",\"Сәлем, name!\",Айша\n", encoding="utf-8")
    n, rejected = locqa.apply_review(tgt, sheet, tmp_path / "o.arb")
    assert n == 0 and rejected


def test_empty_fragment_is_not_reported(tmp_path):
    f = run(tmp_path, {"infoPrefix": "Показатель ", "x": "Дом", "y": "Купить"}, {"infoPrefix": "", "x": "Үй", "y": ""})
    assert not types(f, "infoPrefix")          # пустой кусок составной фразы — намеренно
    assert "untranslated" in types(f, "y")     # обычная пустая строка — ошибка


def test_keep_terms_from_glossary(tmp_path):
    g = "ru,kk,en,comment,kk_wrong,enforce,keep\nМинус Вайб|вайб,Минус Вайб,,,,,yes\n"
    f = run(tmp_path, {"a": "Минус Вайб", "b": "Неизвестная ошибка"}, {"a": "Минус Вайб", "b": "Неизвестная ошибка"}, glossary=g)
    assert not types(f, "a") and "same_as_source" in types(f, "b")


def test_validate(tmp_path):
    tmpl = arb(tmp_path, "intl_ru.arb", "ru", {"a": "Привет, {name}!", "p": "{n, plural, one{x} other{y}}"})
    good = arb(tmp_path, "g.arb", "kk", {"a": "Сәлем, {name}!", "@a": {"x-reviewed_by_human": True},
                                         "p": "{n, plural, other{y}}"})
    bad = arb(tmp_path, "b.arb", "kk", {"a": "Сәлем, {nam}!", "p": "{n, plural, one{x}"})
    assert locqa.validate_arb(good, tmpl)[0] == []
    errs = locqa.validate_arb(bad, tmpl)[0]
    assert any("nam" in e for e in errs) and any("скобка" in e for e in errs)


def test_mark_report_reviewed(tmp_path):
    rep = tmp_path / "report.json"
    rep.write_text(json.dumps([
        {"key": "a", "issue_type": "orthography", "severity": "medium", "source": "x", "target": "y",
         "evidence": "e", "suggestion": "old", "confidence": 0.9, "reviewed_by_human": False},
        {"key": "b", "issue_type": "orthography", "severity": "medium", "source": "x", "target": "y",
         "evidence": "e", "suggestion": None, "confidence": 0.9, "reviewed_by_human": False},
    ], ensure_ascii=False), encoding="utf-8")
    sheet = tmp_path / "review.csv"
    sheet.write_text("key,kk_final,reviewer\na,Жаңа,Айша\nb,Тағы,\n", encoding="utf-8")
    assert locqa.mark_report_reviewed(rep, sheet) == 1
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data[0]["reviewed_by_human"] is True and data[0]["suggestion"] == "Жаңа"
    assert data[1]["reviewed_by_human"] is False
    assert (tmp_path / "report.html").exists()


def test_launcher_runs():
    import subprocess
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, str(root / "locqa"), "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "check" in out.stdout


def test_plural_ru():
    assert [locqa.plural_ru(n, "проблема", "проблемы", "проблем") for n in (1, 2, 5, 11, 21, 72, 277)] == \
        ["проблема", "проблемы", "проблем", "проблем", "проблема", "проблемы", "проблем"]


def test_check_cli_plain_output(tmp_path, capsys):
    src = arb(tmp_path, "intl_ru.arb", "ru", {"a": "Привет, {name}!", "b": "Купить"})
    tgt = arb(tmp_path, "intl_kk.arb", "kk", {"a": "Сәлем, {nam}!", "b": "Сатып алу"})
    out = tmp_path / "r.json"
    code = locqa.main(["check", "--source", str(src), "--target", str(tgt), "--out", str(out), "--no-color"])
    text = capsys.readouterr().out
    assert code == 0 and "\x1b[" not in text
    assert "Найдено 1 проблема" in text and "сломан плейсхолдер" in text
    assert json.loads(out.read_text(encoding="utf-8"))[0]["issue_type"] == "placeholder_mismatch"


def test_apply_preserves_structure(tmp_path):
    src = '{\n  "@@locale": "kk",\n  "@@comment": "A",\n  "a": "Сәлем",\n\n  "@@comment": "B",\n  "b": "x",\n  "@b": {\n    "description": "d"\n  },\n  "c": "y"\n}\n'
    tgt = tmp_path / "intl_kk.arb"
    tgt.write_text(src, encoding="utf-8")
    sheet = tmp_path / "review.csv"
    sheet.write_text('key,kk_final,reviewer\na,"Сәлем, ""дос""",Айша\nb,жаңа,Айша\nc,соңғы,Айша\nz,жоқ еді,Айша\n', encoding="utf-8")
    out = tmp_path / "fixed.arb"
    n, rejected = locqa.apply_review(tgt, sheet, out)
    text = out.read_text(encoding="utf-8")
    data = json.loads(text)
    assert n == 4 and not rejected
    assert text.count('"@@comment"') == 2 and "\n\n" in text            # разделы и пустая строка на месте
    assert data["a"] == 'Сәлем, "дос"' and data["b"] == "жаңа" and data["c"] == "соңғы" and data["z"] == "жоқ еді"
    assert data["@b"]["description"] == "d" and data["@b"]["x-reviewer"] == "Айша"
    assert data["@c"]["x-original"] == "y" and data["@z"]["x-reviewed_by_human"] is True


def test_examples_cover_all_issue_types():
    """examples/ — синтетический пример, где встречаются все 11 типов ошибок из задания."""
    root = Path(__file__).resolve().parent.parent
    findings = locqa.run(root / "examples/intl_ru.arb", root / "examples/intl_kk.arb", root / "glossary.csv",
                         [root / "examples/intl_uz.arb"], loose=False)[1]
    found = {f["issue_type"] for f in findings}
    assert found >= {"untranslated", "same_as_source", "placeholder_mismatch", "plural_mismatch",
                     "glossary_violation", "length_overflow", "machine_translation_suspect",
                     "inconsistent_term", "suffix_harmony", "orthography", "wrong_language"}


def test_transliteration(tmp_path):
    assert locqa.kk_to_latin("Қазақстан") == "Qazaqstan"
    assert locqa.kk_to_latin("ШАҒЫМ ЖАСАУ") == "ŞAĞYM JASAU"
    assert locqa.kk_to_latin("Сәлем, {name}!") == "Sälem, {name}!"     # плейсхолдер не тронут
    assert locqa.kk_to_latin("<g>ЖЕҢІС</g>") == "<g>JEÑIS</g>"            # теги не тронуты
    src = arb(tmp_path, "intl_ru.arb", "ru", {"a": "Победа", "b": "Чай"})
    tgt = arb(tmp_path, "intl_kk.arb", "kk", {"a": "Жеңіс", "b": "Шай"})
    out = tmp_path / "intl_kk_latn.arb"
    n, longer = locqa.transliterate_arb(src, tgt, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert n == 2 and data["a"] == "Jeñıs" and data["@@locale"] == "kk_Latn"
