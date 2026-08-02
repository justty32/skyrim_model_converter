"""nif2gltf CLI — honours PROTOCOL.md. Exit codes: 0 ok / 1 general / 2 parse / 3 skinned."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .gltf_writer import write_gltf
from .nif_reader import NifError, SkinnedNifError, read_nif


def _convert(in_path: str, out_path: str) -> int:
    if not os.path.isfile(in_path):
        print(f"error: cannot read source: {in_path}", file=sys.stderr)
        return 1
    try:
        with open(in_path, "rb") as fh:
            data = fh.read()
        meshes = read_nif(data)
    except SkinnedNifError as exc:
        print(f"skinned: {in_path}: {exc}", file=sys.stderr)
        return 3
    except NifError as exc:
        print(f"parse error: {in_path}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - any backend failure is a general error
        print(f"error: {in_path}: {exc}", file=sys.stderr)
        return 1
    write_gltf(meshes, out_path)
    return 0


def _run_manifest(manifest_path: str, outdir: str | None) -> int:
    if not os.path.isfile(manifest_path):
        print(f"error: cannot read manifest: {manifest_path}", file=sys.stderr)
        return 1
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        items = doc["items"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"error: invalid manifest: {exc}", file=sys.stderr)
        return 1
    base = outdir or "."
    os.makedirs(base, exist_ok=True)
    failures = 0
    for item in items:
        out_path = os.path.join(base, item["out"])
        code = _convert(item["in"], out_path)
        if code != 0:
            failures += 1
            print(f"  -> failed ({code}): {item['in']}", file=sys.stderr)
    print(f"manifest: {len(items) - failures}/{len(items)} converted", file=sys.stderr)
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="nif2gltf",
        description="Skyrim static .nif -> glTF 2.0 preview proxy (MVP: --flat).",
    )
    parser.add_argument("--in", dest="in_path", help="source .nif")
    parser.add_argument("--out", dest="out_path", help="target .gltf (sibling .bin written)")
    parser.add_argument("--flat", action="store_true", help="flat colour, no textures (MVP default)")
    parser.add_argument("--textures", dest="textures", help="texture search root (not in MVP)")
    parser.add_argument("--master", help="source .esm label (annotation only)")
    parser.add_argument("--manifest", help="batch: JSON work-list of {in,out}")
    parser.add_argument("--outdir", help="batch: output directory for manifest items")
    args = parser.parse_args(argv)

    if args.manifest:
        return _run_manifest(args.manifest, args.outdir)

    if not args.in_path or not args.out_path:
        print("error: --in and --out are required (or use --manifest)", file=sys.stderr)
        return 1
    if args.textures:
        print("error: --textures is not implemented in the MVP; use --flat", file=sys.stderr)
        return 1
    return _convert(args.in_path, args.out_path)
