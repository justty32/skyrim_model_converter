"""One-command static asset package, published only after conversion validates."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif

from .errors import AnyError
from .collision import AUTO_MODES
from .normalize import normalize_to_gltf

MANIFEST = "converter-package.json"
SCHEMA = "model-converter-package/1"
_RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10))}


def _asset_name(value):
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower()
    if not name or len(name) > 80:
        raise AnyError("package name needs 1-80 ASCII letters, digits, underscores or hyphens; use --asset-name")
    if name in _RESERVED_NAMES:
        raise AnyError("package name is reserved on Windows; use --asset-name with another name")
    return name


def texture_references(data):
    """Read the texture paths the NIF will actually ask Skyrim to load."""
    header = _read_header(_Reader(data))
    paths = set()
    for block, kind in enumerate(header["types"]):
        if kind != "BSShaderTextureSet":
            continue
        reader = _Reader(data)
        reader.seek(header["offsets"][block])
        for _ in range(reader.u32()):
            path = reader.sized_string()
            if path:
                paths.add(path)
    return sorted(paths)


def _validate(data_dir, nif_path):
    from PIL import Image
    from tex2dds.dds import parse_header, full_mip_count, mip_sizes, linear_size

    data = nif_path.read_bytes()
    meshes = read_nif(data)
    if not meshes or not any(mesh.triangles for mesh in meshes):
        raise AnyError("package validation: NIF contains no triangles")
    paths = texture_references(data)
    for ref in paths:
        relative = Path(ref.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise AnyError(f"package validation: invalid texture path {ref}")
        target = data_dir / relative
        if not target.is_file():
            raise AnyError(f"package validation: missing texture {ref}")
        payload = target.read_bytes()
        header = parse_header(payload)
        width, height = header["width"], header["height"]
        codec = {b"DXT1": "bc1", b"DXT5": "bc3"}.get(header["fourcc"])
        if codec is None or width < 1 or height < 1 or width & (width - 1) or height & (height - 1):
            raise AnyError(f"package validation: DDS must be power-of-two BC1/BC3: {ref}")
        count = full_mip_count(width, height)
        expected_size = 128 + sum(linear_size(w, h, codec) for w, h in mip_sizes(width, height, count))
        if header["mip_count"] != count or len(payload) != expected_size:
            raise AnyError(f"package validation: incomplete DDS mip chain: {ref}")
        with Image.open(target) as image:
            if image.format != "DDS":
                raise AnyError(f"package validation: not DDS: {ref}")
            image.load()
    return paths


def _check_destination(destination, source):
    if destination.is_symlink():
        raise AnyError("package output cannot be a symlink")
    if source == destination or destination in source.parents:
        raise AnyError("package output cannot contain the source model")
    if destination.exists():
        marker = destination / MANIFEST
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AnyError("package output already exists and is not a converter package; choose a new directory")
        if previous.get("schema") != SCHEMA:
            raise AnyError("package output has an unknown manifest; choose a new directory")
        expected = previous.get("sha256")
        actual = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in destination.rglob("*") if p.is_file() and p != marker}
        if any(p.is_symlink() for p in destination.rglob("*")) or expected != actual:
            raise AnyError("package output has user changes; choose a new directory to preserve them")


def convert_package(args):
    """Reuse the existing single-file converter inside an isolated Data tree."""
    from .cli import DEFAULT_TEXPREFIX, main
    from .package_materials import prepare_materials

    try:
        if args.textures_out or args.texprefix != DEFAULT_TEXPREFIX or args.keep_intermediate:
            raise AnyError("--package owns texture paths and temporary files; omit --textures-out, --texprefix and --keep-intermediate")
        source = Path(args.in_path).resolve()
        destination_arg = Path(args.out_path).absolute()
        if destination_arg.is_symlink():
            raise AnyError("package output cannot be a symlink")
        destination = destination_arg.resolve()
        if not source.is_file():
            raise AnyError(f"cannot read source: {source}")
        _check_destination(destination, source)
        name = _asset_name(args.asset_name or source.stem)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".any2nif-package-", dir=destination.parent) as temporary:
            staging = Path(temporary)
            work = staging / "work"
            work.mkdir()
            normalized = normalize_to_gltf(str(source), str(work), fbx2gltf=args.fbx2gltf)
            prepared = prepare_materials(normalized, str(work / "prepared.gltf"))
            data_dir = staging / "data"
            nif = data_dir / "meshes" / "any2nif" / name / f"{name}.nif"
            texture_dir = data_dir / "textures" / "any2nif" / name
            collision = args.collision or "convex"
            forwarded = [prepared, str(nif), "--textures-out", str(texture_dir),
                         "--texprefix", f"textures\\any2nif\\{name}",
                         "--collision", collision, "--up-axis", args.up_axis,
                         "--root-name", args.root_name]
            for flag, value in (("--unit", args.unit), ("--scale", args.scale)):
                if value is not None:
                    forwarded.extend([flag, str(value)])
            if args.no_materials:
                forwarded.append("--no-materials")
            code = main(forwarded)
            if code:
                return code
            refs = _validate(data_dir, nif)
            files = {p.relative_to(data_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(data_dir.rglob("*")) if p.is_file()}
            manifest = {"schema": SCHEMA, "mesh": nif.relative_to(data_dir).as_posix(),
                        "collision": collision if collision == "none" or collision in AUTO_MODES else "json",
                        "textures": refs, "sha256": files}
            (data_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            # Build failures leave the existing package untouched. Publish failure
            # restores the complete old directory, including its manifest.
            # Keep rollback material outside TemporaryDirectory: even an OS error
            # during restoration must not remove the only copy of the old output.
            backup = destination.with_name(f".{destination.name}.previous-{uuid.uuid4().hex}")
            if destination.exists():
                os.replace(destination, backup)
            try:
                os.replace(data_dir, destination)
            except OSError:
                if backup.exists():
                    try:
                        os.replace(backup, destination)
                    except OSError as exc:
                        raise AnyError(f"publish and restore failed; previous package preserved at {backup}: {exc}") from exc
                raise
            if backup.exists():
                try:
                    shutil.rmtree(backup)
                except OSError as exc:
                    print(f"warning: new package published; old backup cleanup failed at {backup}: {exc}", file=sys.stderr)
        print(f"wrote package {destination}: {manifest['mesh']}, {len(refs)} texture(s), collision={manifest['collision']}", file=sys.stderr)
        return 0
    except AnyError as exc:
        print(f"error: package: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:
        print(f"error: package: {exc}", file=sys.stderr)
        return 1
