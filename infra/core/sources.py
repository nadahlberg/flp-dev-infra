"""Where each app's source tree is and which sha it resolved to.

`checkout.py` writes the manifest; this reads it. SOURCES_MANIFEST overrides
the default location (../sources/manifest.json, relative to infra/).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

MANIFEST = Path(os.getenv("SOURCES_MANIFEST", "../sources/manifest.json")).resolve()


@dataclass(frozen=True)
class Source:
    """A checked-out (repo, ref): its directory and resolved commit."""

    path: str
    sha: str

    @property
    def tag(self) -> str:
        """Image tag for this source: the short sha."""
        return self.sha[:7]


def _load() -> dict[str, dict[str, str]]:
    if not MANIFEST.exists():
        raise FileNotFoundError(
            f"{MANIFEST} not found; run `python checkout.py` in infra/ first "
            "(CI does this before pulumi)."
        )
    return json.loads(MANIFEST.read_text())


_manifest = _load()


def source(repo: str, ref: str) -> Source:
    """The checked-out source for `repo` at `ref`."""
    key = f"{repo}@{ref}"
    if key not in _manifest:
        raise KeyError(
            f"{key} is not in {MANIFEST}; re-run `python checkout.py` after "
            "editing deploys.py."
        )
    entry = _manifest[key]
    return Source(path=entry["path"], sha=entry["sha"])
