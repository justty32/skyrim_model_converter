"""Owned-output checks and recoverable directory publication."""

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import sys
import uuid
from contextlib import contextmanager
import tempfile

MANIFEST = "modforge-model-package.json"
SCHEMA = "modforge-model-package/1"
RESERVED = {"con", "prn", "aux", "nul", *(f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10))}


def reject_links(path):
    for part in [path, *path.parents]:
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"symlink/reparse path is not permitted: {part}")


def relative_path(value, folder=None):
    if not isinstance(value, str) or not value:
        raise ValueError("empty/non-string asset path")
    value = value.replace("\\", "/")
    parts = value.split("/")
    if (PureWindowsPath(value).drive or value.startswith("/") or
            any(p in ("", ".", "..") or ":" in p for p in parts) or
            any(p.endswith((".", " ")) for p in parts) or
            any(p.split(".")[0].lower() in RESERVED for p in parts) or
            any(ord(c) < 32 or c in '<>"|?*' for c in value)):
        raise ValueError(f"unsafe relative path: {value}")
    if folder and parts[0].lower() != folder.lower():
        raise ValueError(f"asset must be under {folder}: {value}")
    return "/".join(parts)


def hashes(directory, exclude=()):
    result = {}
    folded = set()
    for path in sorted(directory.rglob("*")):
        reject_links(path)
        if path.is_file():
            rel = relative_path(path.relative_to(directory).as_posix())
            if rel.lower() in folded:
                raise ValueError(f"case-colliding path: {rel}")
            folded.add(rel.lower())
            if rel not in exclude:
                result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def check_destination(destination, source):
    reject_links(destination)
    if destination == source or destination in source.parents:
        raise ValueError("output cannot contain the source")
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("output is not a directory")
        try:
            marker = json.loads((destination / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("output is not an owned model package") from exc
        if (not isinstance(marker, dict) or marker.get("schema") != SCHEMA or
                marker.get("sha256") != hashes(destination, (MANIFEST,))):
            raise ValueError("output is unowned or has user changes; choose a new directory")


@contextmanager
def staging_directory(parent):
    temporary = tempfile.TemporaryDirectory(prefix=".modforge-model-", dir=parent)
    state = {"path": Path(temporary.name), "published": False}
    try:
        yield state
    finally:
        try:
            temporary.cleanup()
        except OSError as exc:
            if not state["published"]:
                raise
            print(f"warning: published; scratch cleanup failed at {temporary.name}: {exc}", file=sys.stderr)


def publish(staged, destination):
    backup = destination.with_name(f".{destination.name}.previous-{uuid.uuid4().hex}")
    if destination.exists():
        os.replace(destination, backup)
    try:
        os.replace(staged, destination)
    except OSError:
        if backup.exists():
            try:
                os.replace(backup, destination)
            except OSError as exc:
                raise OSError(f"publish/restore failed; previous package preserved at {backup}: {exc}") from exc
        raise
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError as exc:
            print(f"warning: published; previous backup cleanup failed at {backup}: {exc}", file=sys.stderr)
