"""OBJ / STL / PLY / DAE / OFF / DXF / XYZ -> glTF, via trimesh. (skeleton)"""

from __future__ import annotations

from .errors import AnyError


def to_gltf(in_path: str, workdir: str) -> str:
    raise AnyError("trimesh backend not implemented yet", 1)
