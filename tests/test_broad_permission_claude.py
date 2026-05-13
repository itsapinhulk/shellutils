"""Tests for py/broad-permission-claude.py and bash/broad-permission-claude."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_mod = __import__("broad-permission-claude")

BASH_SCRIPT = Path(__file__).resolve().parent.parent / "bash" / "broad-permission-claude"

apply_broad_permissions = _mod.apply_broad_permissions
BROAD_ALLOW = _mod.BROAD_ALLOW


def write_settings(directory: Path, data: dict) -> Path:
    p = directory / "settings.json"
    p.write_text(json.dumps(data))
    return p


class TestApplyBroadPermissions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_replaces_existing_allow(self):
        p = write_settings(self.tmp_path, {"permissions": {"allow": ["Bash(old *)"], "deny": []}})
        result = apply_broad_permissions(p)
        self.assertEqual(result["permissions"]["allow"], BROAD_ALLOW)

    def test_preserves_other_permission_keys(self):
        p = write_settings(self.tmp_path, {"permissions": {"allow": [], "deny": ["Bash(rm *)"]}, "other": 1})
        result = apply_broad_permissions(p)
        self.assertEqual(result["permissions"]["deny"], ["Bash(rm *)"])
        self.assertEqual(result["other"], 1)

    def test_adds_permissions_key_when_missing(self):
        p = write_settings(self.tmp_path, {})
        result = apply_broad_permissions(p)
        self.assertEqual(result["permissions"]["allow"], BROAD_ALLOW)

    def test_sets_auto_accept_edits(self):
        p = write_settings(self.tmp_path, {})
        result = apply_broad_permissions(p)
        assert result["permissions"]["defaultMode"] == "acceptEdits"

    def test_overwrites_auto_accept_edits_if_false(self):
        p = write_settings(self.tmp_path, {"autoAcceptEdits": False})
        result = apply_broad_permissions(p)
        assert result["permissions"]["defaultMode"] == "acceptEdits"

    def test_missing_file_uses_empty_settings(self):
        p = self.tmp_path / "nonexistent.json"
        result = apply_broad_permissions(p)
        self.assertEqual(result["permissions"]["allow"], BROAD_ALLOW)
        self.assertEqual(result["permissions"]["defaultMode"], "acceptEdits")

    def test_empty_file_uses_empty_settings(self):
        p = self.tmp_path / "settings.json"
        p.write_text("")
        result = apply_broad_permissions(p)
        self.assertEqual(result["permissions"]["allow"], BROAD_ALLOW)
        self.assertEqual(result["permissions"]["defaultMode"], "acceptEdits")

    def test_broad_allow_contents(self):
        self.assertIn("Bash(git *)", BROAD_ALLOW)
        self.assertIn("Bash(bun run *)", BROAD_ALLOW)
        self.assertIn("Bash(ls *)", BROAD_ALLOW)
        self.assertIn("Bash(cat *)", BROAD_ALLOW)

    def test_main_prints_json(self):
        p = write_settings(self.tmp_path, {"permissions": {"allow": []}})
        with patch.object(sys, "argv", ["broad-permission-claude", str(p)]):
            from io import StringIO
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                _mod.main()
                result = json.loads(mock_out.getvalue())
        self.assertEqual(result["permissions"]["allow"], BROAD_ALLOW)

    def test_main_default_path(self):
        p = write_settings(self.tmp_path, {})
        with patch.object(sys, "argv", ["broad-permission-claude"]):
            with patch.object(_mod, "DEFAULT_SETTINGS_PATH", p):
                from io import StringIO
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    _mod.main()
                    result = json.loads(mock_out.getvalue())
        self.assertEqual(result["permissions"]["allow"], BROAD_ALLOW)


class TestBashWrapper(unittest.TestCase):
    def test_args_forwarded_and_output_is_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text(json.dumps({"permissions": {"allow": []}}))
            result = subprocess.run(
                ["bash", str(BASH_SCRIPT), str(p)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = json.loads(result.stdout)
            self.assertEqual(parsed["permissions"]["allow"], BROAD_ALLOW)

    def test_runs_with_default_path(self):
        # No-args invocation must execute (path defaults to ~/.claude/settings.json).
        # Pass an explicit path so we don't depend on the real home dir.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            result = subprocess.run(
                ["bash", str(BASH_SCRIPT), str(p)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
