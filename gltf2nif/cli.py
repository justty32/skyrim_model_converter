"""gltf2nif CLI — the reverse of nif2gltf. Exit codes: 0 ok / 1 general / 2 parse.

    python -m gltf2nif <in.gltf> <out.nif> [--texprefix textures\\dsport\\m18]
                                           [--collision hulls.json]
"""

from __future__ import annotations

import argparse
import os
import sys

from ._binwriter import GltfError
from .collision import load_hulls
from .gltf_reader import probe_normal_map, read_gltf
from .nif_writer import build_nif

DEFAULT_TEXPREFIX = "textures\\dsport"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="gltf2nif",
        description="glTF static mesh -> Skyrim SSE .nif (reverse of nif2gltf).",
    )
    parser.add_argument("in_path", help="source .gltf / .glb")
    parser.add_argument("out_path", help="target .nif")
    parser.add_argument("--texprefix", default=DEFAULT_TEXPREFIX,
                        help=r"texture path prefix for .dds slots (default: textures\dsport)")
    parser.add_argument("--collision", help="hulls JSON -> bhkConvexVerticesShape collision")
    parser.add_argument("--root-name", default="Scene Root", help="root NiNode name")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.in_path):
        print(f"error: cannot read source: {args.in_path}", file=sys.stderr)
        return 1
    try:
        meshes = read_gltf(args.in_path)
    except GltfError as exc:
        print(f"parse error: {args.in_path}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: {args.in_path}: {exc}", file=sys.stderr)
        return 1

    gltf_dir = os.path.dirname(os.path.abspath(args.in_path))
    normal_flags = [probe_normal_map(gltf_dir, m.material) for m in meshes]

    hulls = None
    if args.collision:
        try:
            hulls = load_hulls(args.collision)
        except (GltfError, OSError, ValueError) as exc:
            print(f"error: collision {args.collision}: {exc}", file=sys.stderr)
            return 1

    try:
        data = build_nif(meshes, args.texprefix, normal_flags, hulls, args.root_name)
    except Exception as exc:  # noqa: BLE001
        print(f"error: writing NIF: {exc}", file=sys.stderr)
        return 1

    out_dir = os.path.dirname(os.path.abspath(args.out_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out_path, "wb") as fh:
        fh.write(data)
    shapes = len(meshes)
    tris = sum(len(m.triangles) for m in meshes)
    hull_note = f", {len(hulls)} collision hull(s)" if hulls else ""
    print(f"wrote {args.out_path}: {shapes} shape(s), {tris} triangles{hull_note}, "
          f"{len(data)} bytes", file=sys.stderr)
    return 0
