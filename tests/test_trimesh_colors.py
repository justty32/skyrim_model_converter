"""Real PLY colour contracts through trimesh normalization and packaged NIF."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh
from pygltflib import GLTF2

from any2nif.normalize import normalize_to_gltf
from gltf2nif import read_gltf
from gltf2nif.geometry import SKYRIM_UNITS_PER_METRE
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif


ROOT = Path(__file__).resolve().parents[1]


def _write_ply(path: Path, *, vertex_colors=None, face_colors=None) -> Path:
    vertices = (
        (0, 0, 0),
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
    )
    faces = ((0, 1, 2), (0, 3, 1))
    lines = [
        "ply", "format ascii 1.0", "element vertex 4",
        "property float x", "property float y", "property float z",
    ]
    if vertex_colors is not None:
        lines += ["property uchar red", "property uchar green",
                  "property uchar blue", "property uchar alpha"]
    lines += ["element face 2", "property list uchar int vertex_indices"]
    if face_colors is not None:
        lines += ["property uchar red", "property uchar green",
                  "property uchar blue", "property uchar alpha"]
    lines.append("end_header")
    for index, vertex in enumerate(vertices):
        row = [*vertex]
        if vertex_colors is not None:
            row += list(vertex_colors[index])
        lines.append(" ".join(map(str, row)))
    for index, face in enumerate(faces):
        row = [3, *face]
        if face_colors is not None:
            row += list(face_colors[index])
        lines.append(" ".join(map(str, row)))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def _normalise(source: Path, tmp_path: Path):
    work = tmp_path / "normalised"
    work.mkdir()
    glb = Path(normalize_to_gltf(str(source), str(work)))
    document = GLTF2().load(str(glb))
    meshes = read_gltf(str(glb))
    assert len(document.meshes) == len(meshes) == 1
    return document, meshes[0]


def _package(source: Path, tmp_path: Path) -> bytes:
    output = tmp_path / "package"
    result = subprocess.run(
        [sys.executable, "-m", "any2nif", str(source), str(output),
         "--package", "--collision", "none"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "converter-package.json").read_text())
    return (output / manifest["mesh"]).read_bytes()


def _shape_layout(data: bytes):
    header = _read_header(_Reader(data))
    shape_index = header["types"].index("BSTriShape")
    shape = header["offsets"][shape_index]
    vertex_desc = struct.unpack_from("<Q", data, shape + 100)[0]
    stride = (vertex_desc & 0xF) * 4
    color_offset = ((vertex_desc >> 24) & 0xF) * 4
    vertex_count = struct.unpack_from("<H", data, shape + 110)[0]
    shader_ref = struct.unpack_from("<i", data, shape + 92)[0]
    shader = header["offsets"][shader_ref]
    return header, shape, vertex_desc, stride, color_offset, vertex_count, shader


def _packed_colors(data: bytes) -> list[tuple[int, int, int, int]]:
    _, shape, _, stride, color_offset, count, _ = _shape_layout(data)
    vertex_data = shape + 116
    return [struct.unpack_from("<4B", data, vertex_data + index * stride + color_offset)
            for index in range(count)]


def _assert_package_geometry(normalized, nif_mesh):
    np.testing.assert_allclose(
        np.asarray(nif_mesh.positions) / SKYRIM_UNITS_PER_METRE,
        normalized.positions, atol=2e-6,
    )
    assert nif_mesh.triangles == normalized.triangles
    if normalized.normals:
        np.testing.assert_allclose(nif_mesh.normals, normalized.normals, atol=0.01)


def test_ply_vertex_rgba_survives_normalize_and_package(tmp_path):
    colors = [(17, 73, 201, 49), (25, 155, 91, 233),
              (220, 31, 99, 128), (7, 240, 143, 3)]
    source = _write_ply(tmp_path / "vertex-colors.ply", vertex_colors=colors)

    document, normalized = _normalise(source, tmp_path)
    assert document.meshes[0].primitives[0].attributes.COLOR_0 is not None
    assert normalized.colors == pytest.approx(np.asarray(colors) / 255.0)

    data = _package(source, tmp_path)
    header, _, vertex_desc, stride, color_offset, count, shader = _shape_layout(data)
    assert count == len(colors)
    assert stride == 32
    assert color_offset == 28
    attributes = vertex_desc >> 44
    assert attributes & 0x20  # VertexAttribute.Vertex_Colors
    assert not attributes & 0x200  # VertexAttribute.Instance
    flags2 = struct.unpack_from("<I", data, shader + 20)[0]
    assert flags2 & 0x20  # SkyrimShaderPropertyFlags2.Vertex_Colors
    assert not flags2 & 0x80  # SkyrimShaderPropertyFlags2.Assume_Shadowmask
    assert not struct.unpack_from("<I", data, shader + 16)[0] & 0x8
    assert _packed_colors(data) == colors

    _assert_package_geometry(normalized, read_nif(data)[0])
    assert "NiAlphaProperty" not in header["types"]


def test_ply_face_rgba_duplicates_shared_vertices_without_averaging_seams(tmp_path):
    face_colors = [(19, 71, 203, 47), (211, 37, 101, 229)]
    source = _write_ply(tmp_path / "face-colors.ply", face_colors=face_colors)
    original = trimesh.load(str(source), process=False)
    expected_positions = np.asarray(original.vertices)[np.asarray(original.faces).reshape(-1)]
    expected_normals = np.asarray(original.vertex_normals)[np.asarray(original.faces).reshape(-1)]
    expected_colors = [face_colors[0]] * 3 + [face_colors[1]] * 3

    document, normalized = _normalise(source, tmp_path)
    assert document.meshes[0].primitives[0].attributes.COLOR_0 is not None
    assert normalized.positions == pytest.approx(expected_positions)
    assert normalized.normals == pytest.approx(expected_normals)
    assert normalized.triangles == [(0, 1, 2), (3, 4, 5)]
    assert normalized.colors == pytest.approx(np.asarray(expected_colors) / 255.0)
    assert normalized.colors[0] != normalized.colors[3]
    assert normalized.colors[1] != normalized.colors[5]

    data = _package(source, tmp_path)
    assert _packed_colors(data) == expected_colors
    _assert_package_geometry(normalized, read_nif(data)[0])


def test_uncolored_ply_stays_without_gltf_or_nif_color_attributes(tmp_path):
    source = _write_ply(tmp_path / "plain.ply")

    document, normalized = _normalise(source, tmp_path)
    assert document.meshes[0].primitives[0].attributes.COLOR_0 is None
    assert normalized.colors == []

    data = _package(source, tmp_path)
    _, _, vertex_desc, stride, color_offset, _, shader = _shape_layout(data)
    assert stride == 28
    assert color_offset == 0
    assert not (vertex_desc >> 44) & 0x20
    assert not (vertex_desc >> 44) & 0x200
    _assert_package_geometry(normalized, read_nif(data)[0])
