"""Geometry: the Mesh IR plus the glTF(Y-up metres) -> Skyrim(Z-up units) maths.

This is the inverse of nif2gltf/geometry.py. That module turns a Skyrim point
(x, y, z) into a glTF point (x, z, -y); we go the other way and additionally
apply the Skyrim unit scale (glTF is metres, Skyrim is ~70 units per metre):

    gltf (X, Y, Z)  ->  skyrim (X, -Z, Y) * SKYRIM_UNITS_PER_METRE

Round-trip note: nif2gltf does NOT divide the scale back out, so a value written
here and read back by nif2gltf returns SKYRIM_UNITS_PER_METRE * original — the
axis is exact, only a known uniform scale differs. Tests assert on that.

Collision hulls are a special case: bhk shapes live in Havok *metres*, and the
DarkSouls hull input is already metres, so hull vertices get the axis swap only
(NO unit scale) — see collision.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Skyrim render units per metre. plan.md pins this at 70.03 (Bethesda's art scale);
# the near-identical Havok constant 69.99 is only used the other way, for collision.
SKYRIM_UNITS_PER_METRE = 70.03


def gltf_to_skyrim_point(x: float, y: float, z: float,
                         scale: float = SKYRIM_UNITS_PER_METRE) -> tuple[float, float, float]:
    """glTF Y-up metres -> Skyrim Z-up units. Inverse of nif2gltf.skyrim_to_gltf_point."""
    return (x * scale, -z * scale, y * scale)


def gltf_to_skyrim_dir(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Direction-only axis swap (no scale) for normals/planes."""
    return (x, -z, y)


@dataclass
class Mesh:
    """One primitive: triangles over a shared vertex list, still in glTF space
    (Y-up metres) — nif_writer applies the Skyrim transform at emit time."""

    name: str = ""
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    # Texture base name from the glTF material (extension stripped), e.g. "m18_wall_07".
    material: str = ""

    @property
    def has_normals(self) -> bool:
        return bool(self.positions) and len(self.normals) == len(self.positions)

    @property
    def has_uvs(self) -> bool:
        return bool(self.positions) and len(self.uvs) == len(self.positions)


def face_normals(positions, triangles) -> list[tuple[float, float, float]]:
    """Per-vertex normals from area-weighted face normals (used when glTF has none)."""
    pos = np.asarray(positions, dtype=np.float64)
    acc = np.zeros_like(pos)
    for a, b, c in triangles:
        n = np.cross(pos[b] - pos[a], pos[c] - pos[a])  # area-weighted (not normalised)
        acc[a] += n
        acc[b] += n
        acc[c] += n
    out = []
    for v in acc:
        ln = float(np.linalg.norm(v))
        out.append((0.0, 0.0, 1.0) if ln < 1e-12 else tuple(v / ln))
    return out


def compute_tangents(positions, normals, uvs, triangles):
    """Per-vertex tangent frame (Lengyel). Returns (tangents, bitangents), each a
    list of unit float3 in the same space as the inputs. BSTriShape needs a tangent
    basis for normal-mapped BSLightingShaderProperty; without UVs we fall back to an
    arbitrary basis orthogonal to the normal."""
    pos = np.asarray(positions, dtype=np.float64)
    nrm = np.asarray(normals, dtype=np.float64)
    n = len(pos)
    tan = np.zeros((n, 3))
    bit = np.zeros((n, 3))
    have_uv = uvs is not None and len(uvs) == n
    if have_uv:
        uv = np.asarray(uvs, dtype=np.float64)
        for a, b, c in triangles:
            e1 = pos[b] - pos[a]
            e2 = pos[c] - pos[a]
            du1 = uv[b] - uv[a]
            du2 = uv[c] - uv[a]
            denom = du1[0] * du2[1] - du2[0] * du1[1]
            f = 1.0 / denom if abs(denom) > 1e-12 else 0.0
            t = f * (du2[1] * e1 - du1[1] * e2)
            for i in (a, b, c):
                tan[i] += t
    tangents, bitangents = [], []
    for i in range(n):
        ni = nrm[i]
        t = tan[i]
        t = t - ni * float(np.dot(ni, t))  # Gram-Schmidt orthogonalise
        ln = float(np.linalg.norm(t))
        if ln < 1e-9:
            # Degenerate: pick any axis not parallel to the normal.
            axis = np.array([1.0, 0.0, 0.0]) if abs(ni[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
            t = np.cross(ni, axis)
            ln = float(np.linalg.norm(t)) or 1.0
        t = t / ln
        b = np.cross(ni, t)
        tangents.append(tuple(t))
        bitangents.append(tuple(b))
    return tangents, bitangents


def convex_hull_planes(verts: np.ndarray, eps: float = 1e-4):
    """Return the outward face half-spaces (normal, offset d) of the convex hull of
    `verts` (Nx3), with plane equation n·x + d = 0 and d = -n·v for v on the face.

    Brute force over vertex triples: a triple defines a hull face iff every other
    vertex lies on one side of its plane. Deduped by direction. O(V^4) — fine for
    V-HACD hull pieces (tens of vertices). No scipy dependency.
    """
    verts = np.asarray(verts, dtype=np.float64)
    n = len(verts)
    if n < 4:
        return []  # degenerate; caller decides (a flat/near-degenerate hull is unusable)
    centered = verts - verts.mean(axis=0)
    if np.linalg.matrix_rank(centered, tol=eps) < 3:
        return []  # coplanar / collinear: zero-volume hull, unusable for a 3-D convex shape
    centroid = verts.mean(axis=0)
    planes: list[tuple[np.ndarray, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                nrm = np.cross(verts[j] - verts[i], verts[k] - verts[i])
                ln = float(np.linalg.norm(nrm))
                if ln < eps:
                    continue
                nrm = nrm / ln
                d = -float(np.dot(nrm, verts[i]))
                # Orient outward (centroid strictly inside).
                if float(np.dot(nrm, centroid)) + d > 0:
                    nrm = -nrm
                    d = -d
                dists = verts @ nrm + d
                if np.all(dists <= eps):  # all points inside -> genuine hull face
                    if not any(float(np.dot(pn, nrm)) > 1.0 - 1e-4 and abs(pd - d) < eps
                               for pn, pd in planes):
                        planes.append((nrm, d))
    return planes
