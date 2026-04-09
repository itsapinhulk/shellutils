"""
test_upgrade_git_submodules.py — unittest test suite for upgrade-git-submodules.

Run with:
    python test/test_upgrade_git_submodules.py

All tests use local-only git repositories in temporary directories — no network
access is required.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parent.parent / "bash" / "upgrade-git-submodules"

# Minimal git environment: override identity so commits work without a global
# ~/.gitconfig, suppress system-level config, and allow file:// transport so
# that local-only submodule operations work on git >= 2.38.
_GIT_ENV_BASE: dict[str, str] = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
    # Allow file:// transport (restricted by default since git 2.38).
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "protocol.file.allow",
    "GIT_CONFIG_VALUE_0": "always",
}


# ---------------------------------------------------------------------------
# Low-level git helpers
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path | None = None, env: dict | None = None) -> str:
    """Run a git command and return stripped stdout. Raises on non-zero exit."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd) if cwd else None,
        env=env or _GIT_ENV_BASE,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    """Initialise a bare-ish git repo with identity config ready for commits."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)


def _make_commit(repo: Path, filename: str = "file.txt", content: str = "initial") -> str:
    """Write *filename* with *content*, commit it, and return the new SHA."""
    (repo / filename).write_text(content)
    _git("add", ".", cwd=repo)
    _git("commit", "-m", f"update {filename}", cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _make_origin_repo(path: Path) -> Path:
    """Create a repo with one initial commit (serves as the submodule origin)."""
    _init_repo(path)
    _make_commit(path, content="initial")
    return path


def _make_parent_repo(path: Path) -> Path:
    """Create a parent git repo with one initial commit."""
    _init_repo(path)
    _make_commit(path, filename="README.md", content="parent")
    return path


def _run_script(
    target: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke upgrade-git-submodules with *target* as the positional argument."""
    env = {**_GIT_ENV_BASE, **(extra_env or {})}
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), str(target)],
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

def _setup_parent_with_submodules(
    tmp: Path,
    submodule_names: tuple[str, ...] = ("sub",),
) -> tuple[Path, dict[str, Path]]:
    """
    Create a parent repo with one submodule per name in *submodule_names*.

    Returns (parent_path, {name: origin_path, ...}).
    """
    origins: dict[str, Path] = {}
    for name in submodule_names:
        origin = tmp / f"origin_{name}"
        _make_origin_repo(origin)
        origins[name] = origin

    parent = _make_parent_repo(tmp / "parent")

    for name, origin in origins.items():
        _git("submodule", "add", str(origin), name, cwd=parent)

    _git("commit", "-m", "add submodules", cwd=parent)
    return parent, origins


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUpgradeGitSubmodules(unittest.TestCase):

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # ------------------------------------------------------------------
    # Basic update
    # ------------------------------------------------------------------

    def test_single_submodule_updated(self) -> None:
        """A single submodule is fast-forwarded to the latest origin commit."""
        parent, origins = _setup_parent_with_submodules(self.tmp)
        new_commit = _make_commit(origins["sub"], content="v2")

        result = _run_script(parent)

        self.assertEqual(result.returncode, 0, result.stderr)
        actual = _git("rev-parse", "HEAD", cwd=parent / "sub")
        self.assertEqual(actual, new_commit)

    def test_multiple_submodules_all_updated(self) -> None:
        """All submodules are updated when no filter is applied."""
        parent, origins = _setup_parent_with_submodules(self.tmp, ("sub1", "sub2"))
        commit1 = _make_commit(origins["sub1"], content="v2")
        commit2 = _make_commit(origins["sub2"], content="v2")

        result = _run_script(parent)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub1"), commit1)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub2"), commit2)

    def test_no_new_commits_is_idempotent(self) -> None:
        """Running the script when origin has no new commits leaves the submodule unchanged."""
        parent, origins = _setup_parent_with_submodules(self.tmp)
        before = _git("rev-parse", "HEAD", cwd=parent / "sub")

        result = _run_script(parent)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub"), before)

    def test_explicit_target_directory_argument(self) -> None:
        """The script accepts an explicit target directory as its first argument."""
        parent, origins = _setup_parent_with_submodules(self.tmp)
        new_commit = _make_commit(origins["sub"], content="v2")

        # Run from the tmp root, passing the parent path explicitly.
        env = {**_GIT_ENV_BASE}
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH), str(parent)],
            cwd=str(self.tmp),
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub"), new_commit)

    # ------------------------------------------------------------------
    # Output messages
    # ------------------------------------------------------------------

    def test_syncing_message_printed(self) -> None:
        """'Syncing <name>...' is printed for each processed submodule."""
        parent, origins = _setup_parent_with_submodules(self.tmp)
        _make_commit(origins["sub"], content="v2")

        result = _run_script(parent)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Syncing sub...", result.stdout)

    def test_skipping_message_printed(self) -> None:
        """'Skipping <name>' is printed for submodules in GIT_SUBMODULES_UPGRADE_SKIP."""
        parent, origins = _setup_parent_with_submodules(self.tmp)
        _make_commit(origins["sub"], content="v2")

        result = _run_script(parent, extra_env={"GIT_SUBMODULES_UPGRADE_SKIP": "sub"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping sub", result.stdout)

    # ------------------------------------------------------------------
    # GIT_SUBMODULES_UPGRADE_TARGET
    # ------------------------------------------------------------------

    def test_target_env_only_updates_named_submodule(self) -> None:
        """GIT_SUBMODULES_UPGRADE_TARGET restricts updates to a single submodule."""
        parent, origins = _setup_parent_with_submodules(self.tmp, ("sub1", "sub2"))
        orig1 = _git("rev-parse", "HEAD", cwd=parent / "sub1")
        _make_commit(origins["sub1"], content="v2")
        commit2 = _make_commit(origins["sub2"], content="v2")

        result = _run_script(parent, extra_env={"GIT_SUBMODULES_UPGRADE_TARGET": "sub2"})

        self.assertEqual(result.returncode, 0, result.stderr)
        # sub2 updated; sub1 untouched.
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub2"), commit2)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub1"), orig1)

    def test_target_env_colon_separated_updates_multiple(self) -> None:
        """Colon-separated GIT_SUBMODULES_UPGRADE_TARGET updates only the listed submodules."""
        parent, origins = _setup_parent_with_submodules(self.tmp, ("sub1", "sub2", "sub3"))
        orig3 = _git("rev-parse", "HEAD", cwd=parent / "sub3")
        commit1 = _make_commit(origins["sub1"], content="v2")
        commit2 = _make_commit(origins["sub2"], content="v2")
        _make_commit(origins["sub3"], content="v2")

        result = _run_script(parent, extra_env={"GIT_SUBMODULES_UPGRADE_TARGET": "sub1:sub2"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub1"), commit1)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub2"), commit2)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub3"), orig3)

    def test_target_env_nonexistent_name_updates_nothing(self) -> None:
        """GIT_SUBMODULES_UPGRADE_TARGET set to an unknown name leaves all submodules unchanged."""
        parent, origins = _setup_parent_with_submodules(self.tmp)
        orig = _git("rev-parse", "HEAD", cwd=parent / "sub")
        _make_commit(origins["sub"], content="v2")

        result = _run_script(parent, extra_env={"GIT_SUBMODULES_UPGRADE_TARGET": "nonexistent"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub"), orig)

    # ------------------------------------------------------------------
    # GIT_SUBMODULES_UPGRADE_SKIP
    # ------------------------------------------------------------------

    def test_skip_env_single_submodule_not_updated(self) -> None:
        """A submodule listed in GIT_SUBMODULES_UPGRADE_SKIP is not updated."""
        parent, origins = _setup_parent_with_submodules(self.tmp, ("sub1", "sub2"))
        orig1 = _git("rev-parse", "HEAD", cwd=parent / "sub1")
        _make_commit(origins["sub1"], content="v2")
        commit2 = _make_commit(origins["sub2"], content="v2")

        result = _run_script(parent, extra_env={"GIT_SUBMODULES_UPGRADE_SKIP": "sub1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub2"), commit2)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub1"), orig1)

    def test_skip_env_colon_separated_skips_multiple(self) -> None:
        """Colon-separated GIT_SUBMODULES_UPGRADE_SKIP skips all listed submodules."""
        parent, origins = _setup_parent_with_submodules(self.tmp, ("sub1", "sub2", "sub3"))
        orig1 = _git("rev-parse", "HEAD", cwd=parent / "sub1")
        orig2 = _git("rev-parse", "HEAD", cwd=parent / "sub2")
        _make_commit(origins["sub1"], content="v2")
        _make_commit(origins["sub2"], content="v2")
        commit3 = _make_commit(origins["sub3"], content="v2")

        result = _run_script(parent, extra_env={"GIT_SUBMODULES_UPGRADE_SKIP": "sub1:sub2"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub3"), commit3)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub1"), orig1)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub2"), orig2)

    # ------------------------------------------------------------------
    # Detached HEAD vs branch mode
    # ------------------------------------------------------------------

    def test_branch_mode_merges_ff_only(self) -> None:
        """Submodule on a branch is updated via fast-forward merge."""
        parent, origins = _setup_parent_with_submodules(self.tmp)

        # Confirm the submodule is on a branch (not detached).
        branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=parent / "sub")
        self.assertNotEqual(branch, "HEAD", "submodule should be on a branch initially")

        new_commit = _make_commit(origins["sub"], content="v2")
        result = _run_script(parent)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub"), new_commit)
        # Should still be on the same branch after the merge.
        self.assertEqual(_git("rev-parse", "--abbrev-ref", "HEAD", cwd=parent / "sub"), branch)

    def test_non_ff_branch_is_rejected(self) -> None:
        """A diverged local branch is not merged: merge --ff-only fails and the script exits non-zero."""
        parent, origins = _setup_parent_with_submodules(self.tmp)

        # Add a local commit to the submodule that origin does not have,
        # then add a different commit to origin — creating a true divergence.
        local_commit = _make_commit(parent / "sub", filename="local.txt", content="local")
        _make_commit(origins["sub"], filename="remote.txt", content="remote")

        result = _run_script(parent)

        self.assertNotEqual(result.returncode, 0)
        # The submodule HEAD must remain at the local commit — not moved.
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub"), local_commit)

    def test_detached_head_uses_checkout(self) -> None:
        """Submodule in detached HEAD state is updated via checkout FETCH_HEAD."""
        parent, origins = _setup_parent_with_submodules(self.tmp)

        # Detach HEAD in the submodule.
        current = _git("rev-parse", "HEAD", cwd=parent / "sub")
        _git("checkout", "--detach", current, cwd=parent / "sub")
        self.assertEqual(
            _git("rev-parse", "--abbrev-ref", "HEAD", cwd=parent / "sub"),
            "HEAD",
        )

        new_commit = _make_commit(origins["sub"], content="v2")
        result = _run_script(parent)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=parent / "sub"), new_commit)

    # ------------------------------------------------------------------
    # Non-recursive: nested submodules are not touched
    # ------------------------------------------------------------------

    def test_nested_submodule_not_updated(self) -> None:
        """The script only updates direct submodules; grandchild submodules are not touched."""
        # Build: grandchild_origin → child_origin (has grandchild as submodule) → parent
        grandchild_origin = self.tmp / "origin_grandchild"
        _make_origin_repo(grandchild_origin)

        child_origin = self.tmp / "origin_child"
        _make_origin_repo(child_origin)
        _git("submodule", "add", str(grandchild_origin), "grandchild", cwd=child_origin)
        _git("commit", "-m", "add grandchild submodule", cwd=child_origin)

        parent = _make_parent_repo(self.tmp / "parent")
        _git("submodule", "add", str(child_origin), "child", cwd=parent)
        _git("submodule", "update", "--init", "--recursive", cwd=parent)
        _git("commit", "-m", "add child submodule", cwd=parent)

        grandchild_path = parent / "child" / "grandchild"
        before = _git("rev-parse", "HEAD", cwd=grandchild_path)

        # Push a new commit to grandchild_origin.
        _make_commit(grandchild_origin, content="v2")

        result = _run_script(parent)

        self.assertEqual(result.returncode, 0, result.stderr)
        # Grandchild must remain at its original commit — not updated.
        self.assertEqual(_git("rev-parse", "HEAD", cwd=grandchild_path), before)


if __name__ == "__main__":
    unittest.main()
