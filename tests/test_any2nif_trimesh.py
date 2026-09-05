"""any2nif.trimesh_backend: OBJ/STL/PLY/DAE/ZAE/OFF/XYZ -> glTF -> gltf2nif.read_gltf.

Format-specific coverage only; CLI dispatch/exit-code contract and the native
glTF/GLB path live in tests/test_any2nif_cli.py. All fixtures here are generated
on the fly (tests/any2nif_fixtures.py) - nothing binary is checked in.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pygltflib import GLTF2

from any2nif.errors import AnyError
from any2nif.trimesh_backend import to_gltf
from gltf2nif import read_gltf
from nif2gltf.nif_reader import read_nif
from tests.any2nif_fixtures import (
    write_dae,
    write_obj_broken,
    write_obj_multi_material,
    write_obj_textured,
    write_off,
    write_ply,
    write_points_xyz,
    write_stl,
    write_zae,
)

ROOT = Path(__file__).resolve().parents[1]


def test_obj_single_material_roundtrip(tmp_path):
    src = write_obj_textured(str(tmp_path / "rock01.obj"))
    glb = to_gltf(src, str(tmp_path))
    assert glb.endswith(".glb")
    assert Path(glb).is_file()

    meshes = read_gltf(glb)
    assert len(meshes) == 1
    mesh = meshes[0]
    assert len(mesh.positions) == 8
    assert len(mesh.triangles) == 12
    assert mesh.has_uvs is True
    assert mesh.material == "rock01"


def test_obj_single_material_embeds_texture(tmp_path):
    src = write_obj_textured(str(tmp_path / "rock01.obj"))
    glb = to_gltf(src, str(tmp_path))
    gltf = GLTF2().load(glb)
    assert len(gltf.images) >= 1


def test_obj_multi_material_yields_multiple_meshes_with_distinct_materials(tmp_path):
    src = write_obj_multi_material(str(tmp_path / "multi.obj"))
    glb = to_gltf(src, str(tmp_path))

    meshes = read_gltf(glb)
    assert len(meshes) == 2
    materials = {m.material for m in meshes}
    assert materials == {"matA", "matB"}
    by_material = {m.material: m for m in meshes}
    assert len(by_material["matA"].triangles) == 12  # box
    assert len(by_material["matB"].triangles) == 80  # icosphere(subdivisions=1)
    assert by_material["matA"].has_uvs is True
    assert by_material["matB"].has_uvs is True

    gltf = GLTF2().load(glb)
    assert len(gltf.images) == 2


def test_stl_binary(tmp_path):
    src = write_stl(str(tmp_path / "box.stl"), ascii=False)
    glb = to_gltf(src, str(tmp_path))
    meshes = read_gltf(glb)
    assert sum(len(m.triangles) for m in meshes) == 12


def test_stl_ascii(tmp_path):
    src = write_stl(str(tmp_path / "box_ascii.stl"), ascii=True)
    glb = to_gltf(src, str(tmp_path))
    meshes = read_gltf(glb)
    assert sum(len(m.triangles) for m in meshes) == 12


def test_ply(tmp_path):
    src = write_ply(str(tmp_path / "box.ply"))
    glb = to_gltf(src, str(tmp_path))
    meshes = read_gltf(glb)
    assert sum(len(m.triangles) for m in meshes) == 12


def test_off(tmp_path):
    src = write_off(str(tmp_path / "box.off"))
    glb = to_gltf(src, str(tmp_path))
    meshes = read_gltf(glb)
    assert sum(len(m.triangles) for m in meshes) == 12


def test_dae_geometry_count_and_fallback_material_name(tmp_path):
    src = write_dae(str(tmp_path / "rock01.dae"))
    glb = to_gltf(src, str(tmp_path))
    meshes = read_gltf(glb)
    assert sum(len(m.triangles) for m in meshes) == 12
    # trimesh's COLLADA loader does not preserve the original material name
    # (see trimesh_backend.py docstring), so it must fall back to the file stem.
    assert {m.material for m in meshes} == {"rock01"}


def test_zae_geometry_count(tmp_path):
    dae = str(tmp_path / "inner.dae")
    zae = str(tmp_path / "rock02.zae")
    write_zae(dae, zae, "rock02")
    glb = to_gltf(zae, str(tmp_path))
    meshes = read_gltf(glb)
    assert sum(len(m.triangles) for m in meshes) == 12


def test_points_xyz_has_no_triangles(tmp_path):
    src = write_points_xyz(str(tmp_path / "cloud.xyz"))
    with pytest.raises(AnyError) as excinfo:
        to_gltf(src, str(tmp_path))
    assert excinfo.value.code == 2
    assert "triangle" in str(excinfo.value)


def test_broken_obj_is_a_parse_or_geometry_error(tmp_path):
    src = write_obj_broken(str(tmp_path / "broken.obj"))
    with pytest.raises(AnyError) as excinfo:
        to_gltf(src, str(tmp_path))
    assert excinfo.value.code == 2


def test_missing_pycollada_is_reported_clearly(tmp_path, monkeypatch):
    src = write_dae(str(tmp_path / "rock01.dae"))

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "collada" or name.startswith("collada."):
            raise ImportError("no module named collada")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(AnyError) as excinfo:
        to_gltf(src, str(tmp_path))
    assert excinfo.value.code == 1
    assert "pycollada" in str(excinfo.value)


def test_end_to_end_cli_obj_to_nif_back_to_gltf(tmp_path):
    src = write_obj_textured(str(tmp_path / "rock01.obj"))
    out = tmp_path / "out.nif"
    proc = subprocess.run(
        [sys.executable, "-m", "any2nif", str(src), str(out)],
        cwd=str(ROOT), capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert out.is_file() and out.stat().st_size > 0

    back = read_nif(out.read_bytes())
    assert sum(len(m.triangles) for m in back) == 12


def test_end_to_end_cli_broken_obj_is_exit_2(tmp_path):
    src = write_obj_broken(str(tmp_path / "broken.obj"))
    out = tmp_path / "out.nif"
    proc = subprocess.run(
        [sys.executable, "-m", "any2nif", str(src), str(out)],
        cwd=str(ROOT), capture_output=True,
    )
    assert proc.returncode == 2, proc.stderr.decode()


def test_end_to_end_cli_unsupported_extension_is_exit_2(tmp_path):
    src = tmp_path / "thing.blend"
    src.write_bytes(b"not a model")
    out = tmp_path / "out.nif"
    proc = subprocess.run(
        [sys.executable, "-m", "any2nif", str(src), str(out)],
        cwd=str(ROOT), capture_output=True,
    )
    assert proc.returncode == 2, proc.stderr.decode()
