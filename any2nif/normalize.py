"""Dispatch a source model file to a backend that yields glTF/GLB on disk.

The whole point of any2nif is that there is exactly ONE path into the NIF writer:
everything becomes glTF first, then gltf2nif.read_gltf does the parsing it already
does (and is already byte-verified for). Backends only have to produce valid glTF.
"""

from __future__ import annotations

import os

from .errors import AnyError

# Handled directly by gltf2nif.read_gltf - no conversion, no round-trip loss.
NATIVE_EXTENSIONS = frozenset({".gltf", ".glb"})
# Handled by any2nif.trimesh_backend.
TRIMESH_EXTENSIONS = frozenset({".obj", ".stl", ".ply", ".dae", ".zae", ".off", ".dxf", ".xyz"})
# Handled by any2nif.fbx_backend (external FBX2glTF binary).
FBX_EXTENSIONS = frozenset({".fbx"})

SUPPORTED_EXTENSIONS = NATIVE_EXTENSIONS | TRIMESH_EXTENSIONS | FBX_EXTENSIONS


def normalize_to_gltf(in_path: str, workdir: str, *, fbx2gltf: str | None = None) -> str:
    """Return a path to a .gltf/.glb equivalent of `in_path`.

    For native glTF input this is `in_path` itself (no copy, no re-encode). For every
    other format a file is written under `workdir`. Raises AnyError.
    """
    if not os.path.isfile(in_path):
        raise AnyError(f"cannot read source: {in_path}", 1)
    ext = os.path.splitext(in_path)[1].lower()
    if ext in NATIVE_EXTENSIONS:
        return in_path
    if ext in FBX_EXTENSIONS:
        from .fbx_backend import to_gltf as fbx_to_gltf
        return fbx_to_gltf(in_path, workdir, fbx2gltf=fbx2gltf)
    if ext in TRIMESH_EXTENSIONS:
        from .trimesh_backend import to_gltf as trimesh_to_gltf
        return trimesh_to_gltf(in_path, workdir)
    known = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    raise AnyError(f"unsupported source extension {ext or '(none)'}; supported: {known}", 2)
