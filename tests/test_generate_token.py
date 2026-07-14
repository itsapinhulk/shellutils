"""Tests for bash/generate-token."""

import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bash" / "generate-token"

TYPE_PATTERNS = {
    "hex": r"[0-9a-f]",
    "numeric": r"[0-9]",
    "alpha": r"[A-Za-z]",
    "alpha-numeric": r"[A-Za-z0-9]",
    "all-caps": r"[A-Z]",
    "all-lower": r"[a-z]",
}


def run(*args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True,
    )


class TestGenerateToken(unittest.TestCase):
    def test_default_is_32_hex_chars(self):
        result = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        token = result.stdout.rstrip("\n")
        self.assertEqual(len(token), 32)
        self.assertRegex(token, r"\A[0-9a-f]+\Z")

    def test_output_ends_with_single_newline(self):
        result = run()
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.rstrip("\n").endswith("\n"))

    def test_custom_length(self):
        for length in (1, 7, 16, 64, 200):
            with self.subTest(length=length):
                result = run(str(length))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(result.stdout.rstrip("\n")), length)

    def test_each_type_charset_and_length(self):
        for type_name, pattern in TYPE_PATTERNS.items():
            with self.subTest(type=type_name):
                result = run("-t", type_name, "50")
                self.assertEqual(result.returncode, 0, result.stderr)
                token = result.stdout.rstrip("\n")
                self.assertEqual(len(token), 50)
                self.assertRegex(token, rf"\A{pattern}+\Z")

    def test_randomness_between_runs(self):
        self.assertNotEqual(run("32").stdout, run("32").stdout)

    def test_help_flag(self):
        result = run("-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)

    def test_rejects_non_integer_length(self):
        result = run("abc")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("positive integer", result.stderr)

    def test_rejects_zero_length(self):
        result = run("0")
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_unknown_type(self):
        result = run("-t", "base64", "8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown type", result.stderr)


if __name__ == "__main__":
    unittest.main()
