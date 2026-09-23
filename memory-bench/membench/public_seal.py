"""The release-wide digest: every published file, not only the data files.

``public_export`` writes ``data/SHA256SUMS`` over the two JSONL files, and
``public/README.md`` tells a downloader to run it before trusting the corpus. That
digest covers the files the release is ABOUT and none of the files it is made of.
The validator is the sharp case: ``public/validator/membench_validate.py`` is the one
file a downloader is told to EXECUTE, and it sat outside the release's own integrity
statement, so a validator edited in transit would clear a corpus it had been edited to
clear. The schema it reads and the README that states the contract were outside too.

So the seal here covers the whole published tree - the README, the schema, the
validator, the data files, and ``data/SHA256SUMS`` itself - at ``public/SHA256SUMS``,
in coreutils format with paths relative to ``public/``. Both digests stay live and
neither replaces the other: ``sha256sum -c SHA256SUMS`` from ``public/`` now checks
the release a downloader actually runs, and the same command from ``public/data``
keeps working for a consumer who took the corpus alone. The outer seal covers the
inner one, so the two cannot drift apart unnoticed.

Sealing is also where the publish set is DECIDED, which is the second job of this
module. A published file is source: the corpus, the schema, the README, and Python a
downloader reads and runs. Anything else is something a build left behind - a
``__pycache__`` directory appears under ``public/validator/`` the moment anything
imports the validator by path - and a release is not a place to ship one. Rather than
filtering strays out of the digest, where they would ship unsealed, ``write_seal``
REFUSES a tree that carries one. A tree that cannot be sealed cannot be published, so
the exclusion holds by construction rather than by a reviewer noticing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

SEAL_FILE = "SHA256SUMS"

# What a published file is. Suffixes, plus the digest files themselves, which are
# named rather than suffixed. Everything else fails to seal, so a new published file
# type is a deliberate edit here and not an accident of what happened to be on disk.
PUBLISHED_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".py"})
PUBLISHED_NAMES = frozenset({SEAL_FILE})

_CHUNK = 1 << 20


def is_publishable(relative_path: str) -> bool:
    """Whether a path inside the published tree is source rather than a build artefact."""
    name = PurePosixPath(relative_path).name
    return name in PUBLISHED_NAMES or PurePosixPath(name).suffix in PUBLISHED_SUFFIXES


def tree_paths(root: Path) -> list[str]:
    """Every file under `root`, as sorted paths relative to it, seal included."""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def sealed_paths(root: Path) -> list[str]:
    """The paths the seal covers: every file except the seal, which cannot hash itself."""
    return [path for path in tree_paths(root) if path != SEAL_FILE]


def unpublishable_paths(root: Path) -> list[str]:
    """Every file under `root` that is not publishable source."""
    return [path for path in sealed_paths(root) if not is_publishable(path)]


def file_digest(path: Path) -> str:
    """The SHA-256 of one file, hex, streamed so a large corpus is not read into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def seal_text(root: Path) -> str:
    """The seal for `root`, in coreutils ``sha256sum`` format, sorted by path.

    Raises ``ValueError`` when the tree carries a file that is not publishable source.
    Refusing here is what keeps a build artefact out of the release: the alternative,
    skipping it, would publish it unsealed under a digest file that claims to cover the
    release."""
    strays = unpublishable_paths(root)
    if strays:
        raise ValueError(
            f"{root} carries {len(strays)} file(s) that are not published source: "
            f"{strays}; a release is sealed over what it publishes, so remove them "
            "rather than sealing around them"
        )
    return "".join(f"{file_digest(root / path)}  {path}\n" for path in sealed_paths(root))


def write_seal(root: Path) -> Path:
    """Write ``<root>/SHA256SUMS`` over every published file, and return its path.

    Idempotent: the seal never covers itself, so re-sealing an unchanged tree rewrites
    the same bytes."""
    target = root / SEAL_FILE
    target.write_text(seal_text(root), encoding="utf-8")
    return target


def parse_seal(text: str) -> dict[str, str]:
    """A coreutils digest listing, as path -> digest.

    Raises ``ValueError`` on a line the format does not produce. A digest file that
    cannot be parsed is not a weaker check, it is no check at all, so it is an error
    rather than a skipped line."""
    entries: dict[str, str] = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        digest, separator, path = line.partition("  ")
        if not separator or not path or len(digest) != 64:
            raise ValueError(f"line {line_no} is not a sha256sum entry: {line!r}")
        entries[path] = digest
    return entries


def verify_seal(root: Path) -> list[str]:
    """Every way `root` disagrees with its own seal, as plain sentences."""
    target = root / SEAL_FILE
    if not target.is_file():
        return [f"{SEAL_FILE} is missing from {root}; the release seals nothing"]
    try:
        published = parse_seal(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{SEAL_FILE}: {exc}"]

    problems: list[str] = []
    on_disk = set(sealed_paths(root))
    for path in sorted(set(published) - on_disk):
        problems.append(f"{path} is sealed and not published")
    for path in sorted(on_disk - set(published)):
        problems.append(f"{path} is published and not sealed")
    for path in sorted(on_disk & set(published)):
        actual = file_digest(root / path)
        if actual != published[path]:
            problems.append(f"{path} hashes to {actual}, and the seal says {published[path]}")
    for path in unpublishable_paths(root):
        problems.append(f"{path} is not published source and must not be in the release")
    return problems


__all__ = [
    "PUBLISHED_NAMES",
    "PUBLISHED_SUFFIXES",
    "SEAL_FILE",
    "file_digest",
    "is_publishable",
    "parse_seal",
    "seal_text",
    "sealed_paths",
    "tree_paths",
    "unpublishable_paths",
    "verify_seal",
    "write_seal",
]
