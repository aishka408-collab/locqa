"""
test_manual_fixes.py
Tests for manual fix validation and application logic.
Verifies placeholder validation and key/file safety.
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.fixes.engine import validate_fix
from qazaq_loc_qa.backend.utils.arb_parser import ARBFile
from qazaq_loc_qa.backend.fixes.models import FixStatus, FixItem


class TestManualFixes:

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.arb_path = self.tmpdir / "test.arb"
        self.arb_path.write_text(
            '{\n'
            '    "greeting": "Сәлем, {name}!",\n'
            '    "msg_count": "Сізде {count} жаңа хабар бар",\n'
            '    "simple": "Жай мәтін"\n'
            '}\n',
            encoding="utf-8"
        )
        self.arb = ARBFile.load(self.arb_path)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_placeholder_validation_for_manual_fix(self):
        """Unified validation for manual and automatic fixes."""
        orig = self.arb.get("greeting")  # "Сәлем, {name}!"

        # 1. Valid manual fix with same placeholder
        valid_fix = "Қайырлы күн, {name}!"
        assert validate_fix(orig, valid_fix) is True

        # 2. Invalid manual fix: renamed placeholder
        invalid_renamed = "Сәлем, {user}!"
        assert validate_fix(orig, invalid_renamed) is False

        # 3. Invalid manual fix: missing placeholder
        invalid_missing = "Сәлем, барлығына!"
        assert validate_fix(orig, invalid_missing) is False

        # 4. Invalid manual fix: extra placeholder
        invalid_extra = "Сәлем, {name}! Сізде {count} балл."
        assert validate_fix(orig, invalid_extra) is False

        # 5. Invalid manual fix: duplicated placeholder count
        invalid_duplicate = "Сәлем {name} және {name}!"
        assert validate_fix(orig, invalid_duplicate) is False

    def test_manual_fix_application_and_key_safety(self):
        """Applying manual fix to correct key updates ARB, while unknown key raises KeyError."""
        out_path = self.tmpdir / "fixed_test.arb"

        # Apply manual fix to existing key
        manual_val = "Қайырлы күн, {name}!"
        assert validate_fix(self.arb.get("greeting"), manual_val) is True
        self.arb.set("greeting", manual_val)
        self.arb.dump(out_path)

        reloaded = ARBFile.load(out_path)
        assert reloaded.get("greeting") == manual_val

        # Applying to non-existent key must raise KeyError
        with pytest.raises(KeyError):
            self.arb.set("non_existent_key", "Мән")

        # Original source file remains untouched
        original_reloaded = ARBFile.load(self.arb_path)
        assert original_reloaded.get("greeting") == "Сәлем, {name}!"

    def test_fix_item_model_supports_manual_status(self):
        """Verify FixItem model supports FixStatus.MANUAL."""
        item = FixItem(
            key="simple",
            category="untranslated",
            source_text="Simple text",
            target_text="Жай мәтін",
            suggested_fix="Қарапайым мәтін",
            reason="Manual correction by reviewer",
            confidence="low",
            status=FixStatus.MANUAL
        )
        assert item.status == FixStatus.MANUAL
        assert item.status.value == "manual"
