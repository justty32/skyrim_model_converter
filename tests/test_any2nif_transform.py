"""Scale validation and mirrored geometry contracts for any2nif."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from any2nif import transform
from any2nif.errors import AnyError
from gltf2nif.geometry import Mesh, SKYRIM_UNITS_PER_METRE
from nif2gltf.nif_reader import read_nif
from tests.gltf2nif_fixtures import write_gltf_interleaved


def _triangle_mesh():
    return Mesh(
        positions=[(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 3.0, 0.0)],
        normals=[(0.0, 0.0, 1.0)] * 3,
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        colors=[(0.1, 0.2, 0.3, 0.4), (0.5, 0.6, 0.7, 0.8), (0.9, 1.0, 0.0, 1.0)],
        triangles=[(0, 1, 2)],
    )


@pytest.mark.parametrize("value", [0.0, float("nan"), float("inf"), -float("inf")])
def test_resolve_scale_rejects_zero_and_non_finite_values(value):
    with pytest.raises(AnyError, match="finite, non-zero"):
        transform.resolve_scale(None, value)


def test_unit_and_scale_multiply_and_underflow_is_rejected():
    assert transform.resolve_scale("cm", -2.5) == pytest.approx(-0.025)
    with pytest.raises(AnyError, match="finite, non-zero"):
        transform.resolve_scale("mm", 5e-324)


def test_negative_uniform_scale_reverses_surface_but_preserves_uvs_and_colours():
    mesh = _triangle_mesh()
    uvs, colors = list(mesh.uvs), list(mesh.colors)

    transform.apply([mesh], scale=-2.0)

    assert mesh.positions == [(0.0, 0.0, 0.0), (-4.0, 0.0, 0.0), (0.0, -6.0, 0.0)]
    assert mesh.normals == [(0.0, 0.0, -1.0)] * 3
    assert mesh.triangles == [(0, 2, 1)]
    assert mesh.uvs == uvs
    assert mesh.colors == colors


def test_positive_scale_keeps_normal_and_winding_behaviour():
    mesh = _triangle_mesh()
    normals, triangles = list(mesh.normals), list(mesh.triangles)

    transform.apply([mesh], scale=2.0)

    assert mesh.positions == [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (0.0, 6.0, 0.0)]
    assert mesh.normals == normals
    assert mesh.triangles == triangles


@pytest.mark.parametrize("value", ["NaN", "Inf", "-Inf", "0"])
def test_cli_rejects_invalid_scale_without_creating_output(tmp_path, value):
    source = tmp_path / "triangle.gltf"
    write_gltf_interleaved(source.as_posix(), [{
        "positions": _triangle_mesh().positions,
        "normals": _triangle_mesh().normals,
        "uvs": _triangle_mesh().uvs,
        "triangles": _triangle_mesh().triangles,
    }])
    output = tmp_path / "bad.nif"

    result = subprocess.run(
        [sys.executable, "-m", "any2nif", str(source), str(output), f"--scale={value}"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )

    assert result.returncode == 1
    assert "finite, non-zero" in result.stderr
    assert not output.exists()


def test_negative_unit_scale_round_trips_as_outward_nif_geometry(tmp_path):
    source = tmp_path / "triangle.gltf"
    mesh = _triangle_mesh()
    write_gltf_interleaved(source.as_posix(), [{
        "positions": mesh.positions,
        "normals": mesh.normals,
        "uvs": mesh.uvs,
        "triangles": mesh.triangles,
    }])
    output = tmp_path / "mirrored.nif"

    result = subprocess.run(
        [sys.executable, "-m", "any2nif", str(source), str(output),
         "--unit", "cm", "--scale", "-2"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    back, = read_nif(output.read_bytes())
    expected_positions = np.asarray(mesh.positions) * -0.02 * SKYRIM_UNITS_PER_METRE
    np.testing.assert_allclose(back.positions, expected_positions, atol=1e-5)
    np.testing.assert_allclose(back.normals, [(0.0, 0.0, -1.0)] * 3, atol=0.01)
    assert back.triangles == [(0, 2, 1)]
    assert back.uvs == pytest.approx(mesh.uvs, abs=1e-3)

    a, b, c = back.triangles[0]
    face_normal = np.cross(np.subtract(back.positions[b], back.positions[a]),
                           np.subtract(back.positions[c], back.positions[a]))
    assert np.dot(face_normal, back.normals[a]) > 0
