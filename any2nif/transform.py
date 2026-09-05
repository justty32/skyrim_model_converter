"""Unit / axis normalisation applied to Mesh IR *before* gltf2nif's Skyrim transform.

gltf2nif.geometry expects glTF convention: Y-up, metres. Real-world source files are
often Z-up (Blender / 3ds Max / most CAD exports) or in centimetres / inches, so
any2nif pre-rotates and pre-scales the IR and leaves gltf2nif's own maths untouched.
Doing it here rather than inside gltf2nif is deliberate: gltf2nif's byte output is
under contract (darksouls-port) and must not change.
"""

from __future__ import annotations

# Multiplier from the named unit to metres.
UNIT_SCALES = {
    "m": 1.0, "metre": 1.0, "meter": 1.0,
    "cm": 0.01, "centimetre": 0.01, "centimeter": 0.01,
    "mm": 0.001,
    "in": 0.0254, "inch": 0.0254,
    "ft": 0.3048, "foot": 0.3048,
}


def resolve_scale(unit: str | None, scale: float | None) -> float:
    """--unit and --scale combine multiplicatively; both optional."""
    factor = 1.0
    if unit:
        key = unit.strip().lower()
        if key not in UNIT_SCALES:
            from .errors import AnyError
            raise AnyError(f"unknown --unit {unit!r}; known: {', '.join(sorted(UNIT_SCALES))}")
        factor *= UNIT_SCALES[key]
    if scale is not None:
        factor *= float(scale)
    return factor


def _zup_to_yup(v):
    """Source Z-up right-handed -> glTF Y-up right-handed: (x, y, z) -> (x, z, -y)."""
    x, y, z = v
    return (x, z, -y)


def apply(meshes, *, scale: float = 1.0, up_axis: str = "y"):
    """Rotate/scale Mesh IR in place-ish (returns the same list) into glTF convention.

    up_axis names the convention of the SOURCE file: "y" (already glTF-like, no-op)
    or "z" (Blender/OBJ-from-CAD style). Scaling touches positions only; normals are
    unit directions and only need the rotation.
    """
    up = (up_axis or "y").strip().lower()
    if up not in ("y", "z"):
        from .errors import AnyError
        raise AnyError(f"--up-axis must be 'y' or 'z', got {up_axis!r}")
    rotate = up == "z"
    if not rotate and scale == 1.0:
        return meshes
    for mesh in meshes:
        if rotate:
            mesh.positions = [_zup_to_yup(p) for p in mesh.positions]
            if mesh.normals:
                mesh.normals = [_zup_to_yup(n) for n in mesh.normals]
        if scale != 1.0:
            mesh.positions = [(x * scale, y * scale, z * scale) for x, y, z in mesh.positions]
    return meshes
