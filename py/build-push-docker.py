#!/usr/bin/env python3
"""
Build a Docker image from a directory and push it to one or more registries.

The image is tagged 'YYYYMMDD-HHMM-<sha>' (UTC) plus ':latest', where <sha>
is the 16-char short SHA of HEAD in the build directory's git repo. If any
existing tag on any target registry already contains <sha>, the build and
push are skipped.

Each registry URL is a full image path (host + repo), e.g.
'ghcr.io/itsapinhulk/devcontainer'. Authentication must be set up before
running (e.g. via `docker login`); the script verifies that the docker
config has an auth entry for each target host and aborts otherwise.

Example:
  %(prog)s ./mydir \\
      --registry ghcr.io/me/app \\
      --registry docker.io/me/app
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run cmd inheriting stdout/stderr; exit cleanly (no traceback) on failure."""
    print(f"+ {' '.join(cmd)}", flush=True)
    res = subprocess.run(cmd, **kwargs)
    if res.returncode != 0:
        sys.exit(f"\nCommand failed (exit {res.returncode}): {' '.join(cmd)}")
    return res


def git_short_sha(directory: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--short=16", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def git_has_uncommitted(directory: Path) -> bool:
    out = subprocess.run(
        ["git", "-C", str(directory), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    )
    return bool(out.stdout.strip())


def registry_host(url: str) -> str:
    return url.split("/", 1)[0]


# Docker Hub is keyed under several aliases in config.json.
DOCKERHUB_ALIASES = (
    "docker.io",
    "index.docker.io",
    "https://index.docker.io/v1/",
)


def _docker_config_auth(host: str) -> str | None:
    """Return the base64 'user:pass' string for host, if present."""
    config_path = Path.home() / ".docker" / "config.json"
    if not config_path.is_file():
        return None
    try:
        cfg = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        return None
    auths = cfg.get("auths") or {}
    keys = DOCKERHUB_ALIASES if host == "docker.io" else (host,)
    for key in keys:
        entry = auths.get(key) or {}
        if entry.get("auth"):
            return entry["auth"]
    return None


def _parse_bearer_challenge(header: str) -> dict[str, str]:
    if not header.lower().startswith("bearer "):
        return {}
    return dict(re.findall(r'(\w+)="([^"]*)"', header[len("bearer "):]))


def list_registry_tags(image_url: str) -> list[str]:
    """Return all tags for image_url via the registry v2 API. [] if repo absent."""
    host, repo = image_url.split("/", 1)
    api_host = "registry-1.docker.io" if host == "docker.io" else host
    url = f"https://{api_host}/v2/{repo}/tags/list"

    def fetch(headers: dict[str, str]) -> bytes:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    try:
        body = fetch({})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        if e.code != 401:
            raise
        challenge = _parse_bearer_challenge(e.headers.get("WWW-Authenticate", ""))
        realm = challenge.get("realm")
        if not realm:
            raise
        params = {k: challenge[k] for k in ("service", "scope") if k in challenge}
        params.setdefault("scope", f"repository:{repo}:pull")
        token_req = urllib.request.Request(f"{realm}?{urllib.parse.urlencode(params)}")
        auth = _docker_config_auth(host)
        if auth:
            token_req.add_header("Authorization", f"Basic {auth}")
        with urllib.request.urlopen(token_req, timeout=15) as token_resp:
            token_data = json.loads(token_resp.read())
        token = token_data.get("token") or token_data.get("access_token")
        if not token:
            raise RuntimeError(f"No token returned by {realm}")
        body = fetch({"Authorization": f"Bearer {token}"})

    return json.loads(body).get("tags") or []


def sha_already_pushed(image_url: str, sha: str) -> str | None:
    """Return the matching tag if any tag on image_url contains sha, else None."""
    for tag in list_registry_tags(image_url):
        if sha in tag:
            return tag
    return None


def verify_logged_in(hosts: list[str]) -> None:
    """Abort unless docker's config has an auth entry for every host."""
    config_path = Path.home() / ".docker" / "config.json"
    auths: dict = {}
    if config_path.is_file():
        try:
            auths = json.loads(config_path.read_text()).get("auths", {}) or {}
        except json.JSONDecodeError:
            sys.exit(f"Could not parse {config_path}")

    # Docker Hub is keyed under several aliases in config.json.
    aliases = {
        "docker.io": {"docker.io", "index.docker.io",
                      "https://index.docker.io/v1/"},
    }

    missing = []
    for host in hosts:
        keys = aliases.get(host, {host})
        if not any(k in auths for k in keys):
            missing.append(host)

    if missing:
        sys.exit(
            "Not logged in to: " + ", ".join(missing)
            + ". Run `docker login <host>` for each before re-running or bypass with --skip-login-check."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "directory",
        help="Build context directory (must be inside a git repo).",
    )
    parser.add_argument(
        "-r", "--registry",
        action="append",
        default=[],
        metavar="URL",
        help="Full image path including host, e.g. 'ghcr.io/my/app'. "
             "May be passed multiple times. If omitted, the image is built "
             "locally with no registry checks, tagging, or push.",
    )
    parser.add_argument(
        "--no-latest",
        action="store_true",
        help="Do not also tag and push ':latest'.",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Build only; skip the login check and push.",
    )
    parser.add_argument(
        "--skip-login-check",
        action="store_true",
        help="Skip verifying docker auth before pushing.",
    )
    parser.add_argument(
        "--skip-push-confirm",
        action="store_true",
        help="Skip the interactive confirmation prompt before pushing.",
    )
    parser.add_argument(
        "--allow-uncommitted",
        action="store_true",
        help="Allow building when the git working tree has uncommitted changes.",
    )
    parser.add_argument(
        "--git-sha",
        metavar="SHA",
        help="Override the git SHA used in the tag (default: 16-char short HEAD).",
    )
    parser.add_argument(
        "--build-date",
        metavar="YYYYMMDD-HHMMSS",
        help="Override the date prefix used in the tag (default: now in UTC).",
    )
    parser.add_argument(
        "--build-arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Forwarded to 'docker build --build-arg'. May repeat.",
    )

    args = parser.parse_args()

    directory = Path(args.directory).expanduser().resolve()
    if not directory.is_dir():
        sys.exit(f"Not a directory: {directory}")

    if args.git_sha and not re.fullmatch(r"[0-9a-f]{7,40}", args.git_sha):
        sys.exit(f"Invalid --git-sha {args.git_sha!r}: expected 7-40 hex chars.")

    if args.build_date and not re.fullmatch(r"\d{8}-\d{6}", args.build_date):
        sys.exit(
            f"Invalid --build-date {args.build_date!r}: expected YYYYMMDD-HHMMSS."
        )

    if (not args.git_sha) and (not args.allow_uncommitted) and git_has_uncommitted(directory):
        sys.exit(
            "Git working tree has uncommitted changes. "
            "Commit or stash them, or pass --allow-uncommitted to bypass."
        )

    sha = args.git_sha or git_short_sha(directory)
    date = args.build_date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    dated_tag = f"{date}-{sha}"
    print(f"Build directory : {directory}")
    print(f"Git short SHA   : {sha}")
    print(f"Image tag       : {dated_tag}")

    def refs_for(url: str) -> list[str]:
        out = [f"{url}:{dated_tag}"]
        if not args.no_latest:
            out.append(f"{url}:latest")
        return out

    all_refs = [ref for url in args.registry for ref in refs_for(url)]

    if args.registry:
        print("\nChecking remote registries for an existing tag with this SHA...")
        for url in args.registry:
            existing = sha_already_pushed(url, sha)
            if existing is not None:
                print(f"  ✓ {url}:{existing} already exists. Nothing to do.")
                return
            print(f"  ✗ {url} (no tag containing {sha})")

        if (not args.no_push) and (not args.skip_login_check):
            hosts = sorted({registry_host(url) for url in args.registry})
            verify_logged_in(hosts)

    tag_args = [arg for ref in all_refs for arg in ("--tag", ref)]
    build_args = [f"GIT_SHA={sha}", f"BUILD_DATE={date}", *args.build_arg]
    build_arg_flags = [arg for ba in build_args for arg in ("--build-arg", ba)]

    print()
    run(["docker", "build", *tag_args, *build_arg_flags, str(directory)])

    if (not args.registry) or args.no_push:
        if args.no_push:
            print("\n--no-push set; skipping push.")
        return

    print()
    for url in args.registry:
        push_to_registry(url, refs_for(url), confirm=not args.skip_push_confirm)

    print("\nDone.")


def push_to_registry(url: str, refs: list[str], *, confirm: bool) -> None:
    if confirm:
        print(f"Ready to push to {url}:")
        for ref in refs:
            print(f"  {ref}")
        answer = input(f"Push to {url}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print(f"Skipped {url}.\n")
            return

    for ref in refs:
        run(["docker", "push", ref])
    print()


if __name__ == "__main__":
    main()
