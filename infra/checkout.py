"""Fetch every (repo, ref) named in deploys.py and write a manifest.

Run before Pulumi, in CI and locally:

    cd infra && python checkout.py [--dir ../sources]

For each unique (repo, ref) this does a shallow fetch of exactly that ref
(a branch, a tag or a sha all work against GitHub), checks it out detached,
and records the resolved sha. Pulumi reads <dir>/manifest.json through
core/sources.py and uses the sha as the image tag.

Stdlib only; needs `git`. Uses GH_TOKEN for the clone URL when set.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import deploys


def _run(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _clone_url(repo: str) -> str:
    token = os.getenv("GH_TOKEN")
    if token:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


def _safe(part: str) -> str:
    return "".join(c if c.isalnum() or c in "-._" else "-" for c in part)


def fetch(repo: str, ref: str, base: Path) -> dict[str, str]:
    """Shallow-fetch `ref` of `repo` into base/<repo>/<ref>; return its entry."""
    dest = base / _safe(repo.replace("/", "--")) / _safe(ref)
    dest.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        _run(["git", "init", "-q"], dest)
        _run(["git", "remote", "add", "origin", _clone_url(repo)], dest)
    else:
        _run(["git", "remote", "set-url", "origin", _clone_url(repo)], dest)
    _run(["git", "fetch", "-q", "--depth", "1", "origin", ref], dest)
    _run(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], dest)
    sha = _run(["git", "rev-parse", "HEAD"], dest)
    return {"path": str(dest.resolve()), "sha": sha}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dir",
        default=os.getenv("SOURCES_DIR", "../sources"),
        help="where to put the checkouts (default: ../sources or $SOURCES_DIR)",
    )
    args = parser.parse_args()
    base = Path(args.dir).resolve()

    deploys.validate()
    manifest: dict[str, dict[str, str]] = {}
    for repo, ref in deploys.sources():
        print(f"fetching {repo} @ {ref} ...", file=sys.stderr)
        entry = fetch(repo, ref, base)
        manifest[f"{repo}@{ref}"] = entry
        print(f"  {entry['sha'][:7]}  {entry['path']}", file=sys.stderr)

    out = base / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
