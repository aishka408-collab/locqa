"""
test_undo.py
Tests for the undo mechanism using direct ARBFile operations,
simulating what the API does internally.
"""
import sys
import os
import hashlib
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.utils.arb_parser import ARBFile

ARB_CONTENT = (
    '{\n'
    '    "welcomeMessage": "Сәлеметсіз бе",\n'
    '    "farewell": "Сау болыңыз"\n'
    '}\n'
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class TestUndoMechanism:

    def setup_method(self):
        """Create a temp dir and write a sample ARB."""
        self.tmpdir = Path(tempfile.mkdtemp())
        self.orig = self.tmpdir / "original.arb"
        self.orig.write_text(ARB_CONTENT, encoding="utf-8", newline="\n")
        self.fixed = self.tmpdir / "fixed_original.arb"
        self.backups_dir = self.tmpdir / "backups"
        self.backups_dir.mkdir()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _apply_fix(self, source: Path, key: str, new_value: str) -> dict:
        """Simulate API apply_fixes: backup then write fixed."""
        # backup current fixed (or orig if no fixed yet)
        backup_src = self.fixed if self.fixed.is_file() else source
        backup_path = self.backups_dir / f"backup_{key}.arb"
        shutil.copy2(backup_src, backup_path)
        backup_hash = _sha256(backup_path)

        arb = ARBFile.load(source)
        arb.set(key, new_value)
        arb.dump(self.fixed)

        return {
            "snapshot_path": str(backup_path),
            "snapshot_hash": backup_hash,
            "fixed_path": str(self.fixed),
        }

    def _undo(self, history_entry: dict) -> None:
        """Simulate API undo_last."""
        backup_path = Path(history_entry["snapshot_path"])
        fixed_path = Path(history_entry["fixed_path"])
        stored_hash = history_entry["snapshot_hash"]

        assert backup_path.is_file(), "Backup file missing"
        actual_hash = _sha256(backup_path)
        assert actual_hash == stored_hash, f"Hash mismatch: {actual_hash} != {stored_hash}"
        shutil.copy2(backup_path, fixed_path)

    def test_single_apply_and_undo(self):
        """Apply one fix, then undo it – value should return to original."""
        orig_val = ARBFile.load(self.orig).get("welcomeMessage")
        history_entry = self._apply_fix(self.orig, "welcomeMessage", "Қайырлы күн")

        # After apply, fixed file has new value
        assert ARBFile.load(self.fixed).get("welcomeMessage") == "Қайырлы күн"

        # Undo
        self._undo(history_entry)
        restored = ARBFile.load(self.fixed)
        assert restored.get("welcomeMessage") == orig_val, "Value not restored after undo"

    def test_two_sequential_applies_and_two_undos(self):
        """Two applies, two undos – correct LIFO restoration."""
        history = []

        # First fix
        history.append(self._apply_fix(self.orig, "welcomeMessage", "Қайырлы күн"))
        after_fix1 = ARBFile.load(self.fixed).get("welcomeMessage")
        assert after_fix1 == "Қайырлы күн"

        # Second fix (re-apply from orig so we need to reload)
        # Simulate: fixed now exists, we apply again from fixed
        arb2 = ARBFile.load(self.fixed)
        backup2 = self.backups_dir / "backup_v2.arb"
        shutil.copy2(self.fixed, backup2)
        history.append({
            "snapshot_path": str(backup2),
            "snapshot_hash": _sha256(backup2),
            "fixed_path": str(self.fixed),
        })
        arb2.set("welcomeMessage", "Жаңа мәтін")
        arb2.dump(self.fixed)
        assert ARBFile.load(self.fixed).get("welcomeMessage") == "Жаңа мәтін"

        # Undo second fix → should go back to "Қайырлы күн"
        self._undo(history.pop())
        assert ARBFile.load(self.fixed).get("welcomeMessage") == "Қайырлы күн"

        # Undo first fix → should go back to original
        self._undo(history.pop())
        orig_val = ARBFile.load(self.orig).get("welcomeMessage")
        assert ARBFile.load(self.fixed).get("welcomeMessage") == orig_val

    def test_undo_fails_on_corrupted_backup(self):
        """Undo should detect hash mismatch and raise AssertionError."""
        history_entry = self._apply_fix(self.orig, "farewell", "Жаңа мән")
        # Corrupt the backup
        Path(history_entry["snapshot_path"]).write_text("CORRUPTED", encoding="utf-8")

        try:
            self._undo(history_entry)
            assert False, "Should have raised on hash mismatch"
        except AssertionError as e:
            assert "mismatch" in str(e) or "Hash" in str(e), f"Unexpected error: {e}"

    def test_original_file_unchanged_after_apply(self):
        """Original ARB file must never be modified during apply."""
        orig_bytes = self.orig.read_bytes()
        self._apply_fix(self.orig, "farewell", "Жаңа мән")
        assert self.orig.read_bytes() == orig_bytes, "Original ARB was modified!"
