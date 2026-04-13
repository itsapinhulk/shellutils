"""Tests for py/backup-dotfiles and bash/backup-dotfiles."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_script = Path(__file__).parent.parent / "py" / "backup-dotfiles.py"
spec = importlib.util.spec_from_file_location("backup_dotfiles", _script)
backup_dotfiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_dotfiles)

encode_path = backup_dotfiles.encode_path
backup = backup_dotfiles.backup

BASH_SCRIPT = Path(__file__).parent.parent / "bash" / "backup-dotfiles"


# --- encode_path ---

class TestEncodePath:
    def test_no_dot(self):
        assert encode_path(Path("config/nvim")) == Path("config/nvim")

    def test_leading_dot_file(self):
        assert encode_path(Path(".bashrc")) == Path("_dot_bashrc")

    def test_leading_dot_directory(self):
        assert encode_path(Path(".config/nvim")) == Path("_dot_config/nvim")

    def test_multiple_dotted_components(self):
        assert encode_path(Path(".config/.nvim")) == Path("_dot_config/_dot_nvim")

    def test_non_leading_dot_unchanged(self):
        assert encode_path(Path("file.txt")) == Path("file.txt")


# --- dry run (default, no --save) ---

class TestDryRun:
    def test_prints_path_without_writing(self, tmp_path, monkeypatch, capsys):
        home = tmp_path / "home"
        src = home / ".bashrc"
        src.parent.mkdir(parents=True)
        src.write_text("export PATH=.")
        monkeypatch.setenv("HOME", str(home))

        target = tmp_path / "backup"
        backup([str(src)], str(target))

        assert not (target / "_tilde_" / "_dot_bashrc").exists()
        assert "_dot_bashrc" in capsys.readouterr().out

    def test_prints_directory_without_writing(self, tmp_path, monkeypatch, capsys):
        home = tmp_path / "home"
        src_dir = home / ".config"
        src_dir.mkdir(parents=True)
        (src_dir / "a.txt").write_text("x")
        monkeypatch.setenv("HOME", str(home))

        target = tmp_path / "backup"
        backup([str(src_dir)], str(target))

        assert not (target / "_tilde_" / "_dot_config").exists()
        assert "_dot_config" in capsys.readouterr().out

    def test_prints_root_path_without_writing(self, tmp_path, monkeypatch, capsys):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        src = tmp_path / "etc" / "hosts"
        src.parent.mkdir(parents=True)
        src.write_text("127.0.0.1 localhost")

        target = tmp_path / "backup"
        backup([str(src)], str(target))

        assert not (target / "_root_").exists()
        assert "_root_" in capsys.readouterr().out

    def test_preamble_shows_target(self, tmp_path, monkeypatch, capsys):
        home = tmp_path / "home"
        src = home / ".bashrc"
        src.parent.mkdir(parents=True)
        src.write_text("")
        monkeypatch.setenv("HOME", str(home))

        target = tmp_path / "backup"
        backup([str(src)], str(target))

        assert str(target.resolve()) in capsys.readouterr().out


# --- backup: home paths (_tilde_) ---

class TestBackupHomePaths:
    def test_file_placed_under_tilde(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        src = home / ".bashrc"
        src.parent.mkdir(parents=True)
        src.write_text("export PATH=.")
        monkeypatch.setenv("HOME", str(home))

        target = tmp_path / "backup"
        backup([str(src)], str(target), save=True)

        assert (target / "_tilde_" / "_dot_bashrc").read_text() == "export PATH=."

    def test_nested_file_preserves_structure(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        src = home / ".config" / "nvim" / "init.lua"
        src.parent.mkdir(parents=True)
        src.write_text("-- config")
        monkeypatch.setenv("HOME", str(home))

        target = tmp_path / "backup"
        backup([str(src)], str(target), save=True)

        assert (target / "_tilde_" / "_dot_config" / "nvim" / "init.lua").read_text() == "-- config"

    def test_directory_copied_recursively(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        src_dir = home / ".config" / "nvim"
        src_dir.mkdir(parents=True)
        (src_dir / "init.lua").write_text("-- config")
        (src_dir / "sub").mkdir()
        (src_dir / "sub" / "plugin.lua").write_text("-- plugin")
        monkeypatch.setenv("HOME", str(home))

        target = tmp_path / "backup"
        backup([str(src_dir)], str(target), save=True)

        dest = target / "_tilde_" / "_dot_config" / "nvim"
        assert (dest / "init.lua").read_text() == "-- config"
        assert (dest / "sub" / "plugin.lua").read_text() == "-- plugin"

    def test_directory_overwritten_on_rerun(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        src_dir = home / ".config"
        src_dir.mkdir(parents=True)
        (src_dir / "a.txt").write_text("v1")
        monkeypatch.setenv("HOME", str(home))

        target = tmp_path / "backup"
        backup([str(src_dir)], str(target), save=True)
        (src_dir / "a.txt").write_text("v2")
        backup([str(src_dir)], str(target), save=True)

        assert (target / "_tilde_" / "_dot_config" / "a.txt").read_text() == "v2"

    def test_multiple_sources_mix_of_file_and_directory(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        src_file = home / ".bashrc"
        src_dir = home / ".config" / "nvim"
        src_file.parent.mkdir(parents=True)
        src_dir.mkdir(parents=True)
        src_file.write_text("# bash")
        (src_dir / "init.lua").write_text("-- nvim")
        monkeypatch.setenv("HOME", str(home))

        target = tmp_path / "backup"
        backup([str(src_file), str(src_dir)], str(target), save=True)

        assert (target / "_tilde_" / "_dot_bashrc").read_text() == "# bash"
        assert (target / "_tilde_" / "_dot_config" / "nvim" / "init.lua").read_text() == "-- nvim"

    def test_nonexistent_source_warns(self, tmp_path, monkeypatch, capsys):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        backup([str(home / ".missing")], str(tmp_path / "backup"))

        assert "Warning" in capsys.readouterr().err


# --- backup: non-home paths (_root_) ---

class TestBackupRootPaths:
    def test_file_placed_under_root(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        src = tmp_path / "etc" / "hosts"
        src.parent.mkdir(parents=True)
        src.write_text("127.0.0.1 localhost")

        target = tmp_path / "backup"
        backup([str(src)], str(target), save=True)

        assert (target / "_root_" / src.relative_to("/")).read_text() == "127.0.0.1 localhost"

    def test_directory_placed_under_root(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        src_dir = tmp_path / "etc" / "app"
        src_dir.mkdir(parents=True)
        (src_dir / "config.conf").write_text("[app]")

        target = tmp_path / "backup"
        backup([str(src_dir)], str(target), save=True)

        assert (target / "_root_" / src_dir.relative_to("/") / "config.conf").read_text() == "[app]"

    def test_dot_encoding_applied_to_root_paths(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        src = tmp_path / ".hidden" / "file.txt"
        src.parent.mkdir(parents=True)
        src.write_text("secret")

        target = tmp_path / "backup"
        backup([str(src)], str(target), save=True)

        assert (target / "_root_" / encode_path(src.relative_to("/"))).read_text() == "secret"


# --- bash wrapper ---

def _run_bash(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        ["bash", str(BASH_SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=merged,
    )


class TestBashWrapper:
    def test_dry_run_prints_paths_without_writing(self, tmp_path):
        home = tmp_path / "home"
        src = home / ".bashrc"
        src.parent.mkdir(parents=True)
        src.write_text("# bashrc")
        target = tmp_path / "backup"

        result = _run_bash(["-s", str(src), "-t", str(target)], env={"HOME": str(home)})

        assert result.returncode == 0
        assert "_dot_bashrc" in result.stdout
        assert "--save" in result.stdout
        assert not (target / "_tilde_" / "_dot_bashrc").exists()

    def test_save_flag_suppresses_dry_run_hint(self, tmp_path):
        home = tmp_path / "home"
        src = home / ".bashrc"
        src.parent.mkdir(parents=True)
        src.write_text("# bashrc")
        target = tmp_path / "backup"

        result = _run_bash(["-s", str(src), "-t", str(target), "--save"], env={"HOME": str(home)})

        assert result.returncode == 0
        assert "--save" not in result.stdout

    def test_save_flag_writes_files(self, tmp_path):
        home = tmp_path / "home"
        src = home / ".bashrc"
        src.parent.mkdir(parents=True)
        src.write_text("# bashrc")
        target = tmp_path / "backup"

        result = _run_bash(["-s", str(src), "-t", str(target), "--save"], env={"HOME": str(home)})

        assert result.returncode == 0
        assert (target / "_tilde_" / "_dot_bashrc").exists()

    def test_env_var_sources_and_target(self, tmp_path):
        home = tmp_path / "home"
        src = home / ".zshrc"
        src.parent.mkdir(parents=True)
        src.write_text("# zshrc")
        target = tmp_path / "backup"

        result = _run_bash(["--save"], env={
            "HOME": str(home),
            "BACKUP_DOTFILES_SOURCES": str(src),
            "BACKUP_DOTFILES_TARGET": str(target),
        })

        assert result.returncode == 0
        assert (target / "_tilde_" / "_dot_zshrc").exists()

    def test_env_var_colon_separated_sources(self, tmp_path):
        home = tmp_path / "home"
        src1 = home / ".bashrc"
        src2 = home / ".zshrc"
        src1.parent.mkdir(parents=True)
        src1.write_text("# bash")
        src2.write_text("# zsh")
        target = tmp_path / "backup"

        result = _run_bash(["--save"], env={
            "HOME": str(home),
            "BACKUP_DOTFILES_SOURCES": f"{src1}:{src2}",
            "BACKUP_DOTFILES_TARGET": str(target),
        })

        assert result.returncode == 0
        assert (target / "_tilde_" / "_dot_bashrc").exists()
        assert (target / "_tilde_" / "_dot_zshrc").exists()

    def test_cli_target_overrides_env_target(self, tmp_path):
        home = tmp_path / "home"
        src = home / ".bashrc"
        src.parent.mkdir(parents=True)
        src.write_text("# bash")
        target_env = tmp_path / "env_backup"
        target_cli = tmp_path / "cli_backup"

        result = _run_bash(
            ["-s", str(src), "-t", str(target_cli), "--save"],
            env={"HOME": str(home), "BACKUP_DOTFILES_TARGET": str(target_env)},
        )

        assert result.returncode == 0
        assert (target_cli / "_tilde_" / "_dot_bashrc").exists()
        assert not target_env.exists()

    def test_root_path_via_bash(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        src = tmp_path / "etc" / "hosts"
        src.parent.mkdir(parents=True)
        src.write_text("127.0.0.1 localhost")
        target = tmp_path / "backup"

        result = _run_bash(
            ["-s", str(src), "-t", str(target), "--save"],
            env={"HOME": str(home)},
        )

        assert result.returncode == 0
        assert (target / "_root_" / src.relative_to("/")).read_text() == "127.0.0.1 localhost"

    def test_missing_sources_and_target_exits_nonzero(self, tmp_path):
        result = _run_bash([], env={
            "BACKUP_DOTFILES_SOURCES": "",
            "BACKUP_DOTFILES_TARGET": "",
        })

        assert result.returncode != 0
