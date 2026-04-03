"""Tests for bash/git-last-updated"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bash" / "git-last-updated"


def run_script(*args, cwd=None):
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


@pytest.fixture()
def git_repo(tmp_path):
    """Minimal git repo with a configured identity."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return tmp_path


def commit(repo, message="commit", date=None):
    env_extra = {}
    if date:
        env_extra = {"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    import os
    env = {**os.environ, **env_extra}
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True, env=env)


class TestGitLastUpdated:
    def test_timestamp_for_file(self, git_repo):
        (git_repo / "a.txt").write_text("hello")
        subprocess.run(["git", "add", "a.txt"], cwd=git_repo, check=True)
        commit(git_repo)

        result = run_script("-C", str(git_repo), "a.txt")
        assert result.returncode == 0
        assert result.stdout.strip().isdigit()

    def test_timestamp_for_directory(self, git_repo):
        subdir = git_repo / "sub"
        subdir.mkdir()
        (subdir / "b.txt").write_text("hi")
        subprocess.run(["git", "add", "sub"], cwd=git_repo, check=True)
        commit(git_repo)

        result = run_script("-C", str(git_repo), "sub")
        assert result.returncode == 0
        assert result.stdout.strip().isdigit()

    def test_reflects_latest_commit(self, git_repo):
        (git_repo / "f.txt").write_text("v1")
        subprocess.run(["git", "add", "f.txt"], cwd=git_repo, check=True)
        commit(git_repo, date="2020-01-01T00:00:00+0000")

        (git_repo / "f.txt").write_text("v2")
        subprocess.run(["git", "add", "f.txt"], cwd=git_repo, check=True)
        commit(git_repo, date="2021-06-01T00:00:00+0000")

        result = run_script("-C", str(git_repo), "f.txt")
        assert result.returncode == 0
        ts = int(result.stdout.strip())
        assert ts == 1622505600  # 2021-06-01 00:00:00 UTC

    def test_untracked_path_exits_nonzero(self, git_repo):
        # Repo needs at least one commit or git log itself errors
        (git_repo / "other.txt").write_text("x")
        subprocess.run(["git", "add", "other.txt"], cwd=git_repo, check=True)
        commit(git_repo)

        result = run_script("-C", str(git_repo), "nonexistent.txt")
        assert result.returncode != 0
        assert "Error" in result.stderr

    def test_human_readable_format(self, git_repo):
        (git_repo / "h.txt").write_text("hi")
        subprocess.run(["git", "add", "h.txt"], cwd=git_repo, check=True)
        commit(git_repo, date="2021-06-01T00:00:00+0000")

        result = run_script("-C", str(git_repo), "-H", "h.txt")
        assert result.returncode == 0
        # date(1) output is not a plain integer
        assert not result.stdout.strip().isdigit()
        assert "2021" in result.stdout

    def test_utc_format(self, git_repo):
        (git_repo / "u.txt").write_text("hi")
        subprocess.run(["git", "add", "u.txt"], cwd=git_repo, check=True)
        commit(git_repo, date="2021-06-01T00:00:00+0000")

        result = run_script("-C", str(git_repo), "-u", "u.txt")
        assert result.returncode == 0
        assert "UTC" in result.stdout
        assert "2021" in result.stdout

    def test_no_args_exits_nonzero(self, git_repo):
        result = run_script(cwd=git_repo)
        assert result.returncode != 0
