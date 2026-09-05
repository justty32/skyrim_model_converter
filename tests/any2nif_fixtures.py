"""Synthetic source-model builders for the trimesh backend tests.

Everything here is generated on the fly with `trimesh.creation` primitives plus
Pillow-made solid-colour textures - nothing binary is checked into the repo.
trimesh is imported lazily inside each builder (same rule as trimesh_backend.py:
it's an optional, slow-importing dependency).
"""

from __future__ import annotations

import os
import zipfile


def _texture_material(name: str, rgb: tuple[int, int, int]):
    """A trimesh SimpleMaterial with an embedded solid-colour PNG, named `name`
    (mirrors what an OBJ/MTL `newmtl <name>` + `map_Kd` pair looks like once
    trimesh has parsed it)."""
    import trimesh
    from PIL import Image

    image = Image.new("RGB", (4, 4), rgb)
    return trimesh.visual.material.SimpleMaterial(image=image, name=name)


def make_textured_box(material_name: str = "rock01", rgb=(200, 40, 40)):
    """A unit box with UVs and one named, textured material."""
    import numpy as np
    import trimesh

    box = trimesh.creation.box()
    uv = np.random.default_rng(0).random((len(box.vertices), 2))
    box.visual = trimesh.visual.TextureVisuals(uv=uv, material=_texture_material(material_name, rgb))
    return box


def make_multi_material_scene():
    """A box (material "matA") and a translated icosphere (material "matB") as
    one trimesh.Scene - the multi-material case."""
    import numpy as np
    import trimesh

    box = make_textured_box("matA", (200, 40, 40))
    sphere = trimesh.creation.icosphere(subdivisions=1)
    sphere.apply_translation([3.0, 0.0, 0.0])
    uv = np.random.default_rng(1).random((len(sphere.vertices), 2))
    sphere.visual = trimesh.visual.TextureVisuals(uv=uv, material=_texture_material("matB", (40, 200, 40)))
    return trimesh.Scene([box, sphere])


def write_obj_textured(path: str, material_name: str = "rock01") -> str:
    """<path> = an .obj (+ sibling .mtl + .png) with one named, textured, UV'd
    material. Returns `path`."""
    box = make_textured_box(material_name)
    box.export(path)
    return path


def write_obj_multi_material(path: str) -> str:
    """<path> = an .obj (+ sibling .mtl + 2 .png) with two objects, each with
    its own named material ("matA", "matB")."""
    scene = make_multi_material_scene()
    scene.export(path)
    return path


def write_obj_broken(path: str) -> str:
    """A file with a .obj extension that is not valid OBJ text at all."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("this is not wavefront obj data @#$%\n\x00\x01garbage")
    return path


def write_stl(path: str, *, ascii: bool = False) -> str:
    import trimesh

    box = trimesh.creation.box()
    box.export(path, file_type="stl_ascii" if ascii else "stl")
    return path


def write_ply(path: str) -> str:
    import trimesh

    box = trimesh.creation.box()
    box.export(path)
    return path


def write_off(path: str) -> str:
    import trimesh

    box = trimesh.creation.box()
    box.export(path)
    return path


def write_dae(path: str, material_name: str = "rock01") -> str:
    """A textured, UV'd box as COLLADA. Note: trimesh's COLLADA exporter does not
    round-trip material names or images (see trimesh_backend.py's docstring) -
    this fixture is for geometry-count and UV-presence checks only."""
    box = make_textured_box(material_name)
    box.export(path)
    return path


def write_zae(dae_path: str, zae_path: str, material_name: str = "rock01") -> str:
    """Zip an already-written .dae into a .zae (trimesh has no direct .zae
    exporter, so this just wraps write_dae's output)."""
    write_dae(dae_path, material_name)
    with zipfile.ZipFile(zae_path, "w") as zf:
        zf.write(dae_path, os.path.basename(dae_path))
    return zae_path


def write_points_xyz(path: str, count: int = 20) -> str:
    """A bare point cloud with no faces at all - `.xyz` is one of the
    TRIMESH_EXTENSIONS but carries no triangle geometry."""
    import random

    rng = random.Random(0)
    with open(path, "w", encoding="utf-8") as fh:
        for _ in range(count):
            fh.write(f"{rng.random()} {rng.random()} {rng.random()}\n")
    return path
