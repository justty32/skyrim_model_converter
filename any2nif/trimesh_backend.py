"""OBJ / STL / PLY / DAE / ZAE / OFF / DXF / XYZ -> glTF, via trimesh.

Everything below funnels into a single `trimesh.Scene`, one geometry per source
material, which is then exported as a GLB. gltf2nif.read_gltf is the ONLY thing
downstream that actually parses geometry, so this module's entire job is making
sure the GLB it hands over is something read_gltf (and, after that,
any2nif/textures.py) already knows how to deal with:

  * one primitive per material (never merged, never silently dropped),
  * the glTF material NAME carrying the texture base name (gltf_reader strips
    the extension off it to get e.g. "m18_wall_07" - see _material_basename),
  * source images embedded into the GLB's buffer, not left as sibling files.

Two of the eight extensions this module claims (TRIMESH_EXTENSIONS in
normalize.py) are dead ends in practice and are not specially handled:
`.dxf` is a 2D vector format in trimesh (Path2D/Path3D), never a Trimesh with
faces, and `.xyz` is a bare point cloud with no faces at all. Both simply fall
through to the "no triangle geometry" AnyError below - there is nothing format-
specific to special-case, so we don't.

Material-name recovery, in order of preference:
  1. The name trimesh attached to the material object. This survives for OBJ/MTL
     (`newmtl <name>`) because trimesh's OBJ loader copies `newmtl` into
     `SimpleMaterial.name`. It does NOT survive for COLLADA (trimesh's DAE/ZAE
     loader builds a fresh PBRMaterial per <effect> and never copies the COLLADA
     material id into `.name` - see trimesh/exchange/dae.py `_parse_material`),
     so DAE/ZAE always fall through to (2) below.
  2. The input file's stem (e.g. "rock01.obj" -> "rock01"), suffixed with the
     geometry's index when a source has more than one material and no name to
     tell them apart (rare in practice - only hand-rolled/COLLADA multi-material
     sources hit this).

Why materials get rebuilt as PBRMaterial before export, instead of just setting
`.name` on whatever trimesh already produced: trimesh's GLB exporter calls
`mat.to_pbr()` on anything that has that method (SimpleMaterial does) and then
reads `.name` off the RESULT - but `SimpleMaterial.to_pbr()` builds a bare
`PBRMaterial(...)` without copying `.name` across, so any name set on the
original SimpleMaterial is silently dropped on the way to GLB. Doing the
to_pbr() conversion ourselves and setting `.name` on the resulting PBRMaterial
(which has no `.to_pbr` of its own, so the exporter uses it verbatim) avoids
that trap.
"""

from __future__ import annotations

import os

from .errors import AnyError

# .zae is a zipped .dae; both go through pycollada.
_COLLADA_EXTENSIONS = frozenset({".dae", ".zae"})


def to_gltf(in_path: str, workdir: str) -> str:
    """Load `in_path` with trimesh and write a .glb equivalent under `workdir`.

    Returns the absolute path to the written .glb. Raises AnyError on any
    failure: unparseable input, a missing optional dependency (pycollada for
    DAE/ZAE), or a source with no triangle geometry at all (point clouds,
    2D-only DXF, an OBJ that failed to parse into any faces).
    """
    ext = os.path.splitext(in_path)[1].lower()
    stem = os.path.splitext(os.path.basename(in_path))[0]

    try:
        import trimesh
    except ImportError as exc:
        raise AnyError(f"trimesh backend requires trimesh; install it into the "
                        f"project venv: {exc}", 1) from exc

    try:
        loaded = trimesh.load(in_path, process=False, group_material=True)
    except ImportError as exc:
        if ext in _COLLADA_EXTENSIONS:
            raise AnyError(f"{ext} requires pycollada; install it into the "
                            f"project venv: {exc}", 1) from exc
        raise AnyError(f"{in_path}: missing optional dependency: {exc}", 1) from exc
    except Exception as exc:  # noqa: BLE001 - any parser failure is a source error
        raise AnyError(f"cannot parse {ext}: {exc}", 2) from exc

    if isinstance(loaded, trimesh.Scene):
        source_geoms = dict(loaded.geometry)
    elif hasattr(loaded, "faces"):
        source_geoms = {stem: loaded}
    else:
        # PointCloud (or anything else without faces at all, e.g. a bare .xyz).
        source_geoms = {}

    mesh_geoms = {name: geom for name, geom in source_geoms.items()
                  if hasattr(geom, "faces") and len(geom.faces)}
    if not mesh_geoms:
        raise AnyError(f"{in_path}: no triangle geometry (point cloud, 2D-only, "
                        "or an empty/unparseable mesh)", 2)

    scene = _assign_materials_and_pack(trimesh, mesh_geoms, stem)

    out_path = os.path.join(workdir, stem + ".glb")
    scene.export(out_path, file_type="glb")
    return out_path


def _assign_materials_and_pack(trimesh, mesh_geoms: dict, stem: str):
    """Give every geometry a PBRMaterial with a stable, meaningful `.name`,
    then pack them into a fresh Scene (one geometry == one glTF primitive ==
    one Mesh once gltf2nif.read_gltf gets hold of it)."""
    from trimesh.visual.color import ColorVisuals
    from trimesh.visual.material import PBRMaterial
    from trimesh.visual.texture import TextureVisuals

    multi = len(mesh_geoms) > 1
    scene = trimesh.Scene()
    for index, (key, geom) in enumerate(mesh_geoms.items()):
        visual = geom.visual
        material = getattr(visual, "material", None)
        source_name = getattr(material, "name", None) if material is not None else None
        final_name = source_name or (stem if not multi else f"{stem}_{index}")

        if material is None or isinstance(visual, ColorVisuals):
            # STL/PLY/OFF etc with no material at all (plain ColorVisuals) - give
            # it a name-only material so gltf_reader still gets a usable base name.
            uv = getattr(visual, "uv", None)
            geom.visual = TextureVisuals(uv=uv, material=PBRMaterial(name=final_name))
        elif isinstance(material, PBRMaterial):
            material.name = final_name
        else:
            # SimpleMaterial (the common OBJ/MTL case) or anything else that
            # knows how to become PBR. Convert ourselves so we control .name -
            # see the docstring above for why letting the exporter do it drops it.
            pbr = material.to_pbr()
            pbr.name = final_name
            visual.material = pbr

        scene.add_geometry(geom, geom_name=key)

    return scene
