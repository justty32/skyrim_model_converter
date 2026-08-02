"""Intermediate geometry representation, decoupled from both NIF and glTF.

A `Mesh` holds geometry already in **glTF space** (Y-up, metres-agnostic):
the NIF reader is responsible for applying NIF node transforms and the
Skyrim(Z-up) -> glTF(Y-up) axis swap before constructing a Mesh, so the
glTF writer stays dumb.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Skyrim is Z-up right-handed; glTF/Godot are Y-up right-handed.
# Rotate -90 deg about X: (x, y, z)_skyrim -> (x, z, -y)_gltf.
def skyrim_to_gltf_point(x: float, y: float, z: float) -> tuple[float, float, float]:
    return (x, z, -y)


# Same rotation applies to direction vectors (normals).
def skyrim_to_gltf_dir(x: float, y: float, z: float) -> tuple[float, float, float]:
    return (x, z, -y)


@dataclass
class Mesh:
    """One renderable shape: triangles over a shared vertex list (glTF space)."""

    name: str = ""
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    # Triangles as triples of vertex indices into `positions`.
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    # Diffuse texture path as declared in the NIF (e.g. "textures\\...\\foo.dds"), or "".
    texture: str = ""

    @property
    def has_normals(self) -> bool:
        return bool(self.positions) and len(self.normals) == len(self.positions)

    @property
    def has_uvs(self) -> bool:
        return bool(self.positions) and len(self.uvs) == len(self.positions)

    def is_empty(self) -> bool:
        return not self.positions or not self.triangles
