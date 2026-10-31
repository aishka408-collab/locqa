"""
test_persistence.py
Tests that analysis state is correctly saved and restored via state.py.
Simulates restart by calling load_analyses() after save_analyses().
"""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))


class TestPersistence:

    def setup_method(self):
        """Use a temp dir for state files."""
        self.tmpdir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_module(self):
        """Import state module with patched STATE_DIR."""
        import importlib
        import qazaq_loc_qa.backend.api.state as state_mod
        # Patch the paths dynamically
        state_mod.STATE_DIR = self.tmpdir
        state_mod.ANALYSES_PATH = self.tmpdir / "analyses.json"
        return state_mod

    def test_save_and_load_round_trip(self):
        """Data saved should be identical when loaded."""
        state_mod = self._get_module()
        data = {
            "abc123": {
                "file_ids": ["file1.arb"],
                "errors": [],
                "fixes": {"key1": {"action": "accept", "new_value": "Жаңа"}},
                "history": [{"timestamp": "2026-10-31", "description": "test"}],
            }
        }
        state_mod.save_analyses(data)
        loaded = state_mod.load_analyses()
        assert loaded == data, f"Round-trip mismatch: {loaded}"

    def test_load_returns_empty_dict_when_no_file(self):
        """load_analyses returns {} when file does not exist."""
        state_mod = self._get_module()
        result = state_mod.load_analyses()
        assert result == {}, f"Expected empty dict, got: {result}"

    def test_load_handles_corrupt_file(self):
        """Corrupt state must be surfaced rather than silently replaced."""
        state_mod = self._get_module()
        state_mod.ANALYSES_PATH.write_text("NOT_JSON", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Persisted analysis state is invalid"):
            state_mod.load_analyses()

    def test_multiple_saves_do_not_accumulate(self):
        """Second save completely replaces the first (no accumulation)."""
        state_mod = self._get_module()
        state_mod.save_analyses({"old_key": {}})
        state_mod.save_analyses({"new_key": {"fixes": {}}})
        loaded = state_mod.load_analyses()
        assert "old_key" not in loaded, "Old key should not persist after overwrite"
        assert "new_key" in loaded

    def test_fix_status_preserved(self):
        """Fix statuses (accept, reject, manual) are preserved exactly."""
        state_mod = self._get_module()
        data = {
            "analysis1": {
                "file_ids": [],
                "errors": [],
                "fixes": {
                    "key_accept": {"action": "accept", "new_value": "val1"},
                    "key_reject": {"action": "reject", "new_value": None},
                    "key_manual": {"action": "manual", "new_value": "custom val"},
                },
                "history": [],
            }
        }
        state_mod.save_analyses(data)
        loaded = state_mod.load_analyses()
        fixes = loaded["analysis1"]["fixes"]
        assert fixes["key_accept"]["action"] == "accept"
        assert fixes["key_reject"]["action"] == "reject"
        assert fixes["key_manual"]["action"] == "manual"
        assert fixes["key_manual"]["new_value"] == "custom val"
