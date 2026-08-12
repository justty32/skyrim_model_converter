"""gltf2nif reader: parse real (synthetic) interleaved glTF into Mesh IR."""

from __future__ import annotations

import base64
import math
import struct

import pytest
from pygltflib import (
    ARRAY_BUFFER, FLOAT, GLTF2, UNSIGNED_BYTE, VEC2, VEC3,
    Accessor, Attributes, Buffer, BufferView, Node, Primitive, Scene,
)
from pygltflib import Mesh as GltfMesh

from gltf2nif._binwriter import GltfError
from gltf2nif.gltf_reader import probe_normal_map, read_gltf
from tests.gltf2nif_fixtures import CUBE_POS, CUBE_TRIS, write_gltf_interleaved


def test_read_interleaved_single(tmp_path):
    p = str(tmp_path / "one.gltf")
    write_gltf_interleaved(p, [{
        "positions": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        "normals": [(0, 0, 1)] * 3,
        "uvs": [(0, 0), (1, 0), (0, 1)],
        "triangles": [(0, 1, 2)],
        "material": "m18_wall_07.tga",
    }])
    meshes = read_gltf(p)
    assert len(meshes) == 1
    m = meshes[0]
    assert len(m.positions) == 3
    assert m.triangles == [(0, 1, 2)]
    assert m.has_normals and m.has_uvs
    assert m.material == "m18_wall_07"  # .tga extension stripped
    assert m.positions[1] == pytest.approx((1.0, 0.0, 0.0))
    assert m.uvs[2] == pytest.approx((0.0, 1.0))


def test_read_multi_primitive(tmp_path):
    p = str(tmp_path / "two.gltf")
    write_gltf_interleaved(p, [
        {"positions": [tuple(map(float, q)) for q in CUBE_POS],
         "normals": [(0, 0, 1)] * 8, "uvs": [(0, 0)] * 8,
         "triangles": CUBE_TRIS, "material": "a"},
        {"positions": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
         "normals": [(0, 0, 1)] * 3, "uvs": [(0, 0), (1, 0), (0, 1)],
         "triangles": [(0, 1, 2)], "material": "b"},
    ])
    meshes = read_gltf(p)
    assert len(meshes) == 2
    assert {m.material for m in meshes} == {"a", "b"}
    assert len(meshes[0].triangles) == 12


def test_read_empty_raises(tmp_path):
    p = str(tmp_path / "empty.gltf")
    from pygltflib import GLTF2
    GLTF2().save_json(p)
    with pytest.raises(GltfError):
        read_gltf(p)


def test_probe_normal_map(tmp_path):
    (tmp_path / "wall_n.dds").write_bytes(b"\x00")
    assert probe_normal_map(str(tmp_path), "wall") is True
    assert probe_normal_map(str(tmp_path), "other") is False
    assert probe_normal_map(str(tmp_path), "") is False


def _triangle_file(tmp_path, name="scene.gltf"):
    path = str(tmp_path / name)
    write_gltf_interleaved(path, [{
        "positions": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        "normals": [(1, 1, 0)] * 3,
        "uvs": [(0, 0), (1, 0), (0, 1)],
        "triangles": [(0, 1, 2)], "material": "stone",
    }])
    return path


def test_active_scene_hierarchy_and_instancing_are_baked(tmp_path):
    path = _triangle_file(tmp_path)
    gltf = GLTF2().load(path)
    gltf.nodes = [
        Node(translation=[100, 0, 0], children=[1, 2]),
        Node(mesh=0, name="first", translation=[1, 0, 0], scale=[2, 1, 1]),
        Node(mesh=0, name="second", translation=[0, 3, 0]),
        Node(mesh=0, name="inactive", translation=[999, 0, 0]),
    ]
    gltf.scenes = [Scene(nodes=[3]), Scene(nodes=[0])]
    gltf.scene = 1
    gltf.save_json(path)

    meshes = read_gltf(path)

    assert [mesh.name for mesh in meshes] == ["first", "second"]
    assert meshes[0].positions[1] == pytest.approx((103, 0, 0))
    assert meshes[1].positions[0] == pytest.approx((100, 3, 0))


def test_node_matrix_precedes_trs_and_is_column_major(tmp_path):
    path = _triangle_file(tmp_path)
    gltf = GLTF2().load(path)
    # 90 degrees around Z plus translation (4, 5, 6), encoded column-major.
    gltf.nodes[0].matrix = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 4, 5, 6, 1]
    gltf.nodes[0].translation = [999, 999, 999]
    gltf.save_json(path)

    mesh = read_gltf(path)[0]

    assert mesh.positions[1] == pytest.approx((4, 6, 6))


def test_normals_use_inverse_transpose_and_mirror_flips_winding(tmp_path):
    path = _triangle_file(tmp_path)
    gltf = GLTF2().load(path)
    gltf.nodes[0].scale = [-2, 1, 1]
    gltf.save_json(path)

    mesh = read_gltf(path)[0]

    expected = (-0.5, 1.0, 0.0)
    length = math.sqrt(expected[0] ** 2 + expected[1] ** 2)
    assert mesh.normals[0] == pytest.approx(tuple(v / length for v in expected))
    assert mesh.triangles == [(0, 2, 1)]


def test_points_mode_is_not_treated_as_triangles(tmp_path):
    path = _triangle_file(tmp_path)
    gltf = GLTF2().load(path)
    gltf.meshes[0].primitives[0].mode = 0
    gltf.save_json(path)

    with pytest.raises(GltfError, match="no triangle geometry"):
        read_gltf(path)


def test_normalized_integer_accessors_are_scaled(tmp_path):
    path = tmp_path / "normalized.gltf"
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    nrm = struct.pack("<9f", 0, 0, 1, 0, 0, 1, 0, 0, 1)
    uv = bytes([0, 0, 255, 0, 0, 255])
    blob = pos + nrm + uv
    uri = "data:application/octet-stream;base64," + base64.b64encode(blob).decode("ascii")
    gltf = GLTF2(
        scene=0, scenes=[Scene(nodes=[0])], nodes=[Node(mesh=0)],
        meshes=[GltfMesh(primitives=[Primitive(attributes=Attributes(
            POSITION=0, NORMAL=1, TEXCOORD_0=2))])],
        buffers=[Buffer(byteLength=len(blob), uri=uri)],
        bufferViews=[
            BufferView(buffer=0, byteOffset=0, byteLength=len(pos), target=ARRAY_BUFFER),
            BufferView(buffer=0, byteOffset=len(pos), byteLength=len(nrm), target=ARRAY_BUFFER),
            BufferView(buffer=0, byteOffset=len(pos) + len(nrm), byteLength=len(uv), target=ARRAY_BUFFER),
        ],
        accessors=[
            Accessor(bufferView=0, componentType=FLOAT,
                     count=3, type=VEC3),
            Accessor(bufferView=1, componentType=FLOAT,
                     count=3, type=VEC3),
            Accessor(bufferView=2, componentType=UNSIGNED_BYTE, normalized=True,
                     count=3, type=VEC2),
        ],
    )
    gltf.save_json(str(path))

    mesh = read_gltf(str(path))[0]

    assert mesh.positions[1] == pytest.approx((1, 0, 0))
    assert mesh.normals[0] == pytest.approx((0, 0, 1))
    assert mesh.uvs[1] == pytest.approx((1, 0))


def test_integer_normal_without_quantization_extension_is_rejected(tmp_path):
    path = _triangle_file(tmp_path)
    gltf = GLTF2().load(path)
    gltf.accessors[gltf.meshes[0].primitives[0].attributes.NORMAL].componentType = UNSIGNED_BYTE
    gltf.accessors[gltf.meshes[0].primitives[0].attributes.NORMAL].normalized = True
    gltf.save_json(path)

    with pytest.raises(GltfError, match="NORMAL must be"):
        read_gltf(path)


def test_empty_active_scene_does_not_export_mesh_library(tmp_path):
    path = _triangle_file(tmp_path)
    gltf = GLTF2().load(path)
    gltf.scenes = [Scene(nodes=[])]
    gltf.scene = 0
    gltf.save_json(path)

    with pytest.raises(GltfError, match="no triangle geometry"):
        read_gltf(path)


def test_invalid_triangle_index_is_rejected(tmp_path):
    path = _triangle_file(tmp_path)
    bin_path = tmp_path / "scene.bin"
    data = bytearray(bin_path.read_bytes())
    data[-4:] = (99).to_bytes(4, "little")
    bin_path.write_bytes(data)

    with pytest.raises(GltfError, match="out-of-range index"):
        read_gltf(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda gltf: setattr(gltf.nodes[0], "skin", 0), "uses skinning"),
        (lambda gltf: setattr(gltf.meshes[0].primitives[0], "targets", [{"POSITION": 0}]),
         "uses morph targets"),
        (lambda gltf: setattr(gltf, "animations", [{}]), "animated glTF"),
    ],
)
def test_non_static_gltf_is_rejected(tmp_path, mutate, message):
    path = _triangle_file(tmp_path)
    gltf = GLTF2().load(path)
    mutate(gltf)
    gltf.save_json(path)

    with pytest.raises(GltfError, match=message):
        read_gltf(path)


@pytest.mark.parametrize("keep_nodes", [False, True])
def test_asset_without_scene_is_rejected(tmp_path, keep_nodes):
    path = _triangle_file(tmp_path)
    gltf = GLTF2().load(path)
    gltf.scenes = []
    gltf.scene = None
    if not keep_nodes:
        gltf.nodes = []
    gltf.save_json(path)

    with pytest.raises(GltfError, match="has no scene"):
        read_gltf(path)
