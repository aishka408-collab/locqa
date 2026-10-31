"""
test_arb_integrity.py
Tests that ARBFile preserves key order, comments, UTF-8, LF line endings,
special symbols, and never overwrites the original.
"""
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.utils.arb_parser import ARBFile, ARBEntry


ARB_CONTENT = (
    '{\n'
    '    "@@locale": "kk",\n'
    '    "@@comment1": "First comment",\n'
    '    "welcomeMessage": "Сәлеметсіз бе",\n'
    '    "@#InlineComment": "ignored",\n'
    '    "currency": "₸ валюта",\n'
    '    "placeholder": "Сәлем {name}, сізде {count} хабар бар",\n'
    '    "@@comment2": "Second comment",\n'
    '    "farewell": "Сау болыңыз"\n'
    '}\n'
)


class TestARBIntegrity:

    def test_key_order_preserved(self):
        with tempfile.NamedTemporaryFile(suffix=".arb", delete=False, mode="w", encoding="utf-8", newline="\n") as f:
            f.write(ARB_CONTENT)
            src = Path(f.name)
        try:
            arb = ARBFile.load(src)
            keys = arb.keys()
            assert keys == ["welcomeMessage", "currency", "placeholder", "farewell"], \
                f"Key order not preserved: {keys}"
        finally:
            src.unlink()

    def test_comments_preserved_after_dump(self):
        with tempfile.NamedTemporaryFile(suffix=".arb", delete=False, mode="w", encoding="utf-8", newline="\n") as f:
            f.write(ARB_CONTENT)
            src = Path(f.name)
        out = src.with_suffix(".out.arb")
        try:
            arb = ARBFile.load(src)
            arb.dump(out)
            reloaded = ARBFile.load(out)
            comment_keys = [e.key for e in reloaded.entries if e.type == "comment"]
            assert "@@locale" in comment_keys, "@@locale comment missing after dump"
            assert "@@comment1" in comment_keys, "@@comment1 missing after dump"
            assert "@@comment2" in comment_keys, "@@comment2 missing after dump"
            assert "@#InlineComment" in comment_keys, "@#InlineComment should be a comment"
        finally:
            src.unlink()
            if out.exists():
                out.unlink()

    def test_utf8_and_special_chars_preserved(self):
        with tempfile.NamedTemporaryFile(suffix=".arb", delete=False, mode="w", encoding="utf-8", newline="\n") as f:
            f.write(ARB_CONTENT)
            src = Path(f.name)
        out = src.with_suffix(".out.arb")
        try:
            arb = ARBFile.load(src)
            arb.dump(out)
            reloaded = ARBFile.load(out)
            assert reloaded.get("currency") == "₸ валюта", "Currency symbol/Cyrillic lost"
            assert reloaded.get("welcomeMessage") == "Сәлеметсіз бе", "Kazakh characters lost"
        finally:
            src.unlink()
            if out.exists():
                out.unlink()

    def test_lf_line_endings(self):
        with tempfile.NamedTemporaryFile(suffix=".arb", delete=False, mode="w", encoding="utf-8", newline="\n") as f:
            f.write(ARB_CONTENT)
            src = Path(f.name)
        out = src.with_suffix(".out.arb")
        try:
            arb = ARBFile.load(src)
            arb.set("farewell", "Қош келдіңіз")
            arb.dump(out)
            raw_bytes = out.read_bytes()
            assert b"\r\n" not in raw_bytes, "CRLF line endings found – expected LF only"
        finally:
            src.unlink()
            if out.exists():
                out.unlink()

    def test_original_not_modified_after_set(self):
        with tempfile.NamedTemporaryFile(suffix=".arb", delete=False, mode="w", encoding="utf-8", newline="\n") as f:
            f.write(ARB_CONTENT)
            src = Path(f.name)
        original_content = src.read_bytes()
        out = src.with_suffix(".out.arb")
        try:
            arb = ARBFile.load(src)
            arb.set("farewell", "CHANGED VALUE")
            arb.dump(out)
            # Original file must be untouched
            assert src.read_bytes() == original_content, "Original ARB file was modified!"
        finally:
            src.unlink()
            if out.exists():
                out.unlink()
