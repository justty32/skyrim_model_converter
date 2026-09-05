r"""any2nif CLI - one command from any common model format to a Skyrim SSE .nif.

    python -m any2nif <in> <out.nif> [--textures-out DIR] [--texprefix textures\x\y]
                      [--scale F] [--unit m|cm|mm|in|ft] [--up-axis y|z]
                      [--collision hulls.json] [--fbx2gltf PATH] [--no-materials]
                      [--keep-intermediate DIR]

Exit codes match PROTOCOL.md: 0 ok / 1 general / 2 source parse failure /
3 skinned or animated source (static backend refuses).
"""

from __future__ import annotations

import argparse
import inspect
import os
import shutil
import sys
import tempfile

from gltf2nif import GltfError, build_nif, load_hulls, read_gltf
from gltf2nif.gltf_reader import probe_normal_map

from . import transform
from .errors import AnyError
from .normalize import SUPPORTED_EXTENSIONS, normalize_to_gltf

DEFAULT_TEXPREFIX = "textures\\any2nif"


def _build(meshes, texprefix, normal_flags, hulls, root_name, material_specs):
    """Call gltf2nif.build_nif, passing material specs only if this build supports it.

    Keeps any2nif working against a gltf2nif that has not (yet) grown the material
    parameter, instead of hard-failing on a TypeError.
    """
    if material_specs and "material_specs" in inspect.signature(build_nif).parameters:
        return build_nif(meshes, texprefix, normal_flags, hulls, root_name,
                         material_specs=material_specs)
    return build_nif(meshes, texprefix, normal_flags, hulls, root_name)


def _material_specs(gltf_path: str, meshes):
    """Read PBR material specs for each mesh, if gltf2nif.material is available."""
    try:
        from gltf2nif.material import specs_for_meshes
    except ImportError:
        return None
    try:
        return specs_for_meshes(gltf_path, meshes)
    except Exception:  # noqa: BLE001 - material extraction must never sink a conversion
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="any2nif",
        description="Common model formats (OBJ/GLB/glTF/FBX/DAE/STL/PLY/OFF) -> "
                    "Skyrim SSE .nif, with optional .dds texture export.",
    )
    parser.add_argument("in_path", help="source model (%s)" %
                        ", ".join(sorted(SUPPORTED_EXTENSIONS)))
    parser.add_argument("out_path", help="target .nif")
    parser.add_argument("--textures-out", metavar="DIR",
                        help="write the source's textures here as .dds (BC1/BC3 + mipmaps)")
    parser.add_argument("--texprefix", default=DEFAULT_TEXPREFIX,
                        help=r"in-game texture path prefix written into the NIF "
                             r"(default: textures\any2nif)")
    parser.add_argument("--scale", type=float, default=None,
                        help="extra uniform scale applied to source coordinates")
    parser.add_argument("--unit", default=None,
                        help="source unit: m (default), cm, mm, in, ft")
    parser.add_argument("--up-axis", default="y", choices=("y", "z"),
                        help="up axis of the SOURCE file (default y, the glTF convention)")
    parser.add_argument("--collision", help="hulls JSON -> bhkConvexVerticesShape collision")
    parser.add_argument("--root-name", default="Scene Root", help="root NiNode name")
    parser.add_argument("--fbx2gltf", help="path to the FBX2glTF binary (FBX input only)")
    parser.add_argument("--no-materials", action="store_true",
                        help="ignore source PBR material values; use gltf2nif's static defaults")
    parser.add_argument("--keep-intermediate", metavar="DIR",
                        help="keep the normalised glTF here instead of a temp dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.in_path):
        print(f"error: cannot read source: {args.in_path}", file=sys.stderr)
        return 1
    try:
        scale = transform.resolve_scale(args.unit, args.scale)
    except AnyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code

    keep = args.keep_intermediate
    workdir = keep or tempfile.mkdtemp(prefix="any2nif-")
    os.makedirs(workdir, exist_ok=True)
    try:
        try:
            gltf_path = normalize_to_gltf(args.in_path, workdir, fbx2gltf=args.fbx2gltf)
        except AnyError as exc:
            print(f"error: {args.in_path}: {exc}", file=sys.stderr)
            return exc.code

        try:
            meshes = read_gltf(gltf_path)
        except GltfError as exc:
            message = str(exc)
            code = 3 if ("skinning" in message or "morph" in message or
                         "animated" in message) else 2
            print(f"parse error: {args.in_path}: {message}", file=sys.stderr)
            return code
        except Exception as exc:  # noqa: BLE001
            print(f"error: {args.in_path}: {exc}", file=sys.stderr)
            return 1

        transform.apply(meshes, scale=scale, up_axis=args.up_axis)

        gltf_dir = os.path.dirname(os.path.abspath(gltf_path))
        written: dict[str, dict[str, str]] = {}
        if args.textures_out:
            from .textures import export_textures
            try:
                os.makedirs(args.textures_out, exist_ok=True)
                written = export_textures(gltf_path, args.textures_out)
            except Exception as exc:  # noqa: BLE001
                print(f"error: texture export: {exc}", file=sys.stderr)
                return 1

        if written:
            normal_flags = [bool(written.get(m.material, {}).get("normal")) for m in meshes]
        else:
            normal_flags = [probe_normal_map(gltf_dir, m.material) for m in meshes]

        hulls = None
        if args.collision:
            try:
                hulls = load_hulls(args.collision)
            except (GltfError, OSError, ValueError) as exc:
                print(f"error: collision {args.collision}: {exc}", file=sys.stderr)
                return 1

        specs = None if args.no_materials else _material_specs(gltf_path, meshes)

        try:
            data = _build(meshes, args.texprefix, normal_flags, hulls, args.root_name, specs)
        except Exception as exc:  # noqa: BLE001
            print(f"error: writing NIF: {exc}", file=sys.stderr)
            return 1

        out_dir = os.path.dirname(os.path.abspath(args.out_path))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out_path, "wb") as fh:
            fh.write(data)
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)

    tris = sum(len(m.triangles) for m in meshes)
    tex_note = f", {sum(len(v) for v in written.values())} texture(s)" if written else ""
    hull_note = f", {len(hulls)} collision hull(s)" if hulls else ""
    print(f"wrote {args.out_path}: {len(meshes)} shape(s), {tris} triangles"
          f"{tex_note}{hull_note}, {len(data)} bytes", file=sys.stderr)
    return 0
