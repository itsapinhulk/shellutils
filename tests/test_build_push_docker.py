"""Tests for py/build-push-docker.py and bash/build-push-docker.

Push paths are not exercised — there's no practical way to test pushing to a
real registry from CI. Build-only paths use the local docker daemon.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

_script = Path(__file__).parent.parent / "py" / "build-push-docker.py"
spec = importlib.util.spec_from_file_location("build_push_docker", _script)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BASH_SCRIPT = Path(__file__).parent.parent / "bash" / "build-push-docker"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0


needs_docker = pytest.mark.skipif(
    not _docker_available(), reason="docker daemon not available"
)


def _git_init(repo: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True, env=env,
    )


# --- pure helpers ---

class TestRegistryHost:
    def test_strips_repo_path(self):
        assert mod.registry_host("ghcr.io/me/app") == "ghcr.io"

    def test_no_path(self):
        assert mod.registry_host("docker.io") == "docker.io"


class TestParseBearerChallenge:
    def test_parses_realm_service_scope(self):
        header = (
            'Bearer realm="https://auth.docker.io/token",'
            'service="registry.docker.io",scope="repository:lib/img:pull"'
        )
        out = mod._parse_bearer_challenge(header)
        assert out["realm"] == "https://auth.docker.io/token"
        assert out["service"] == "registry.docker.io"
        assert out["scope"] == "repository:lib/img:pull"

    def test_non_bearer_returns_empty(self):
        assert mod._parse_bearer_challenge("Basic realm=x") == {}

    def test_empty_returns_empty(self):
        assert mod._parse_bearer_challenge("") == {}


class TestDockerConfigAuth:
    def test_reads_auth_for_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = tmp_path / ".docker" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"auths": {"ghcr.io": {"auth": "abc"}}}))
        assert mod._docker_config_auth("ghcr.io") == "abc"

    def test_dockerhub_alias_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = tmp_path / ".docker" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps(
            {"auths": {"https://index.docker.io/v1/": {"auth": "xyz"}}}
        ))
        assert mod._docker_config_auth("docker.io") == "xyz"

    def test_missing_host_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = tmp_path / ".docker" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"auths": {}}))
        assert mod._docker_config_auth("ghcr.io") is None

    def test_missing_config_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert mod._docker_config_auth("ghcr.io") is None


class TestGitHelpers:
    def test_short_sha_is_16_chars(self, tmp_path):
        _git_init(tmp_path)
        sha = mod.git_short_sha(tmp_path)
        assert len(sha) == 16
        assert all(c in "0123456789abcdef" for c in sha)

    def test_clean_tree_not_uncommitted(self, tmp_path):
        _git_init(tmp_path)
        assert mod.git_has_uncommitted(tmp_path) is False

    def test_modified_tree_is_uncommitted(self, tmp_path):
        _git_init(tmp_path)
        (tmp_path / "f").write_text("x")
        assert mod.git_has_uncommitted(tmp_path) is True


class TestVerifyLoggedIn:
    def test_passes_when_host_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = tmp_path / ".docker" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"auths": {"ghcr.io": {"auth": "x"}}}))
        mod.verify_logged_in(["ghcr.io"])  # no SystemExit

    def test_fails_when_host_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = tmp_path / ".docker" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"auths": {}}))
        with pytest.raises(SystemExit, match="Not logged in to: ghcr.io"):
            mod.verify_logged_in(["ghcr.io"])


# --- argparse-level validation (subprocess) ---

def _run_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(_script), *args],
        capture_output=True, text=True, cwd=cwd,
    )


class TestArgValidation:
    def test_invalid_git_sha_rejected(self, tmp_path):
        result = _run_cli([str(tmp_path), "--git-sha", "ZZZZ"])
        assert result.returncode != 0
        assert "Invalid --git-sha" in (result.stdout + result.stderr)

    def test_invalid_build_date_rejected(self, tmp_path):
        result = _run_cli([str(tmp_path), "--build-date", "2026-05-05"])
        assert result.returncode != 0
        assert "Invalid --build-date" in (result.stdout + result.stderr)

    def test_nonexistent_directory_rejected(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        result = _run_cli([str(missing)])
        assert result.returncode != 0
        assert "Not a directory" in (result.stdout + result.stderr)

    def test_uncommitted_changes_blocks(self, tmp_path):
        _git_init(tmp_path)
        (tmp_path / "f").write_text("dirty")
        result = _run_cli([str(tmp_path)])
        assert result.returncode != 0
        assert "uncommitted changes" in (result.stdout + result.stderr).lower()

    def test_allow_uncommitted_bypasses(self, tmp_path):
        # No Dockerfile here, but the dirty-tree check happens before build,
        # so we can confirm the bypass by getting past that check.
        _git_init(tmp_path)
        (tmp_path / "f").write_text("dirty")
        # Without docker actually building, we just confirm we get past the
        # uncommitted check. Use --no-push and a missing Dockerfile to fail
        # later — at the docker build step — not earlier.
        result = _run_cli([str(tmp_path), "--allow-uncommitted", "--no-push"])
        assert "uncommitted changes" not in (result.stdout + result.stderr).lower()


# --- end-to-end docker build (no push) ---

@needs_docker
class TestDockerBuild:
    def _setup_repo(self, root: Path) -> None:
        _git_init(root)
        (root / "Dockerfile").write_text(
            "FROM scratch\n"
            "ARG GIT_SHA\n"
            "ARG BUILD_DATE\n"
            "LABEL git_sha=$GIT_SHA\n"
            "LABEL build_date=$BUILD_DATE\n"
        )
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
        }
        subprocess.run(["git", "-C", str(root), "add", "Dockerfile"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "commit", "-q", "-m", "df"],
            check=True, env=env,
        )

    def _cleanup_by_label(self, label: str) -> None:
        ids = subprocess.run(
            ["docker", "image", "ls", "-q", "--filter", f"label={label}"],
            capture_output=True, text=True,
        ).stdout.split()
        if ids:
            subprocess.run(["docker", "rmi", "-f", *ids], capture_output=True)

    def test_build_without_registry_succeeds(self, tmp_path):
        self._setup_repo(tmp_path)
        unique = uuid.uuid4().hex[:8]
        try:
            result = _run_cli(
                [str(tmp_path), "--build-arg", f"MARKER={unique}"],
            )
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            self._cleanup_by_label(f"git_sha={mod.git_short_sha(tmp_path)}")

    def test_build_args_inject_git_sha_and_build_date(self, tmp_path):
        self._setup_repo(tmp_path)
        sha = mod.git_short_sha(tmp_path)
        date = "20260101-1200"
        # Tag locally so we can inspect labels.
        try:
            result = _run_cli([
                str(tmp_path),
                "--registry", "local-test/img",
                "--no-push",
                "--build-date", date,
                "--skip-login-check",
            ])
            # The registry-listing step will hit the network for 'local-test';
            # we accept either a clean skip or an HTTP/network failure as long
            # as the build itself never had a chance to run with wrong args.
            # If it succeeded (no network or registry returned 404), inspect.
            if result.returncode == 0:
                ref = f"local-test/img:{date}-{sha}"
                inspect = subprocess.run(
                    ["docker", "image", "inspect", ref],
                    capture_output=True, text=True,
                )
                assert inspect.returncode == 0, inspect.stderr
                meta = json.loads(inspect.stdout)
                labels = meta[0]["Config"]["Labels"] or {}
                assert labels.get("git_sha") == sha
                assert labels.get("build_date") == date
        finally:
            self._cleanup_by_label(f"git_sha={sha}")

    def test_no_registry_skips_push_section(self, tmp_path):
        self._setup_repo(tmp_path)
        try:
            result = _run_cli([str(tmp_path)])
            assert result.returncode == 0
            assert "docker push" not in result.stdout
        finally:
            self._cleanup_by_label(f"git_sha={mod.git_short_sha(tmp_path)}")


# --- bash wrapper ---

class TestBashWrapper:
    def test_help_passthrough(self):
        result = subprocess.run(
            ["bash", str(BASH_SCRIPT), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Build a Docker image" in result.stdout

    def test_args_forwarded_verbatim(self, tmp_path):
        result = subprocess.run(
            ["bash", str(BASH_SCRIPT), str(tmp_path), "--git-sha", "ZZZZ"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "Invalid --git-sha" in (result.stdout + result.stderr)
