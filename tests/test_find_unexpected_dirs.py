"""Tests for py/find-unexpected-dirs.py and bash/find-unexpected-dirs."""

import subprocess
import sys
from pathlib import Path

_mod = __import__("find-unexpected-dirs")

find_unexpected_dirs = _mod.find_unexpected_dirs
DEFAULT_NAMES = _mod.DEFAULT_NAMES
main = _mod.main

BASH_SCRIPT = Path(__file__).resolve().parent.parent / "bash" / "find-unexpected-dirs"


class TestFindUnexpectedDirs:
    def test_finds_non_empty_default_names(self, tmp_path):
        venv = tmp_path / "project" / ".venv"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("")

        node_modules = tmp_path / "project" / "frontend" / "node_modules"
        node_modules.mkdir(parents=True)
        (node_modules / "some-pkg").mkdir()

        results = set(find_unexpected_dirs(tmp_path, DEFAULT_NAMES))
        assert results == {venv, node_modules}

    def test_skips_empty_matching_dirs(self, tmp_path):
        empty_venv = tmp_path / "project" / ".venv"
        empty_venv.mkdir(parents=True)

        results = set(find_unexpected_dirs(tmp_path, DEFAULT_NAMES))
        assert results == set()

    def test_custom_names(self, tmp_path):
        target = tmp_path / "project" / "build"
        target.mkdir(parents=True)
        (target / "out.txt").write_text("")

        venv = tmp_path / "project" / ".venv"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("")

        results = set(find_unexpected_dirs(tmp_path, ["build"]))
        assert results == {target}

    def test_prune_skips_nested_matches_by_default(self, tmp_path):
        outer = tmp_path / "node_modules"
        outer.mkdir()
        (outer / "pkg").mkdir()

        inner = outer / "pkg" / "node_modules"
        inner.mkdir()
        (inner / "subpkg").mkdir()

        results = set(find_unexpected_dirs(tmp_path, ["node_modules"], prune=True))
        assert results == {outer}

    def test_no_prune_finds_nested_matches(self, tmp_path):
        outer = tmp_path / "node_modules"
        outer.mkdir()
        (outer / "pkg").mkdir()

        inner = outer / "pkg" / "node_modules"
        inner.mkdir()
        (inner / "subpkg").mkdir()

        results = set(find_unexpected_dirs(tmp_path, ["node_modules"], prune=False))
        assert results == {outer, inner}

    def test_no_matches_returns_empty(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")

        results = set(find_unexpected_dirs(tmp_path, DEFAULT_NAMES))
        assert results == set()


class TestMain:
    def test_prints_matching_dirs(self, tmp_path, capsys):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("")

        argv_backup = sys.argv
        try:
            sys.argv = ["find-unexpected-dirs", str(tmp_path)]
            main()
        finally:
            sys.argv = argv_backup

        out = capsys.readouterr().out.splitlines()
        assert out == [str(venv)]

    def test_default_path_is_cwd(self, tmp_path, capsys, monkeypatch):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("")

        monkeypatch.chdir(tmp_path)
        argv_backup = sys.argv
        try:
            sys.argv = ["find-unexpected-dirs"]
            main()
        finally:
            sys.argv = argv_backup

        out = capsys.readouterr().out.splitlines()
        assert out == [str(Path(".venv"))]

    def test_custom_name_via_cli(self, tmp_path, capsys):
        target = tmp_path / "build"
        target.mkdir()
        (target / "out.txt").write_text("")

        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("")

        argv_backup = sys.argv
        try:
            sys.argv = ["find-unexpected-dirs", str(tmp_path), "-n", "build"]
            main()
        finally:
            sys.argv = argv_backup

        out = capsys.readouterr().out.splitlines()
        assert out == [str(target)]

    def test_repeated_name_via_cli(self, tmp_path, capsys):
        a = tmp_path / "build"
        a.mkdir()
        (a / "out.txt").write_text("")

        b = tmp_path / "dist"
        b.mkdir()
        (b / "out.txt").write_text("")

        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("")

        argv_backup = sys.argv
        try:
            sys.argv = ["find-unexpected-dirs", str(tmp_path), "-n", "build", "-n", "dist"]
            main()
        finally:
            sys.argv = argv_backup

        out = capsys.readouterr().out.splitlines()
        assert sorted(out) == sorted([str(a), str(b)])

    def test_not_a_directory_exits(self, tmp_path):
        missing = tmp_path / "nope"

        argv_backup = sys.argv
        try:
            sys.argv = ["find-unexpected-dirs", str(missing)]
            try:
                main()
            except SystemExit as e:
                assert "Not a directory" in str(e)
            else:
                raise AssertionError("expected SystemExit")
        finally:
            sys.argv = argv_backup


class TestBashWrapper:
    def test_help_passthrough(self):
        result = subprocess.run(
            ["bash", str(BASH_SCRIPT), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Find non-empty directories" in result.stdout

    def test_args_forwarded_verbatim(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("")

        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "pkg").mkdir()

        result = subprocess.run(
            ["bash", str(BASH_SCRIPT), str(tmp_path), "-n", ".venv"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(venv)
