"""FBX -> glTF, via the external FBX2glTF binary. (skeleton)"""

from __future__ import annotations

from .errors import AnyError


def find_fbx2gltf(explicit: str | None = None) -> str | None:
    return None


def to_gltf(in_path: str, workdir: str, *, fbx2gltf: str | None = None) -> str:
    raise AnyError("fbx backend not implemented yet", 1)
