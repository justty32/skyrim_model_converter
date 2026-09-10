"""Contracts for preserving supplied glTF tangent frames through any2nif."""

import base64
import json
import struct

import numpy as np
import pytest

from any2nif.cli import main
from any2nif.mesh_split import split_meshes
from any2nif.transform import apply
from gltf2nif import read_gltf
from gltf2nif.geometry import Mesh
from gltf2nif.nif_writer import build_nif
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header
from tests.test_any2nif_package import _snapshot, _source


def _add_tangents(path, values, *, accessor_type="VEC4", count=None,
                  accessor_index=None, remove_normal=False):
    doc = json.loads(path.read_text())
    primitive = doc["meshes"][0]["primitives"][0]
    if remove_normal:
        primitive["attributes"].pop("NORMAL", None)
    if accessor_index is not None:
        primitive["attributes"]["TANGENT"] = accessor_index
        path.write_text(json.dumps(doc))
        return
    data_path = path.with_name(doc["buffers"][0]["uri"])
    raw = data_path.read_bytes()
    tangent = np.asarray(values, dtype="<f4").tobytes()
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(raw),
                               "byteLength": len(tangent)})
    doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1,
                             "componentType": 5126,
                             "count": len(values) if count is None else count,
                             "type": accessor_type})
    primitive["attributes"]["TANGENT"] = len(doc["accessors"]) - 1
    doc["buffers"][0]["byteLength"] = len(raw) + len(tangent)
    data_path.write_bytes(raw + tangent)
    path.write_text(json.dumps(doc))


def _frames(data):
    """Return stored tangent/bitangent frames for every BSTriShape."""
    header = _read_header(_Reader(data))
    actual_types = header["types"]

    def snorm(value):
        return value / 127.5 - 1.0

    result = []
    for block, kind in enumerate(actual_types):
        if kind != "BSTriShape":
            continue
        start = header["offsets"][block]
        descriptor = struct.unpack_from("<Q", data, start + 100)[0]
        stride = (descriptor & 0xF) * 4
        count = struct.unpack_from("<H", data, start + 110)[0]
        vertices = start + 116
        frames = []
        for index in range(count):
            offset = vertices + index * stride
            bx = struct.unpack_from("<f", data, offset + 12)[0]
            nx, ny, nz, by, tx, ty, tz, bz = data[offset + 20:offset + 28]
            skyrim = np.column_stack(((snorm(tx), snorm(ty), snorm(tz)),
                                      (bx, snorm(by), snorm(bz)),
                                      (snorm(nx), snorm(ny), snorm(nz))))
            frames.append(skyrim[[0, 2, 1], :] * np.array([[1], [1], [-1]]))
        result.append(np.asarray(frames))
    return result


@pytest.mark.parametrize(("mutation", "message"), [
    ({"accessor_index": 999}, "TANGENT"),
    ({"accessor_index": True}, "TANGENT"),
    ({"accessor_type": "VEC3"}, "VEC4"),
    ({"count": 7}, "POSITION"),
    ({"values": [(float("nan"), 0, 0, 1)] * 8}, "finite"),
    ({"values": [(0, 0, 0, 1)] * 8}, "nonzero"),
    ({"values": [(0, 1, 0, 1)] * 8}, "parallel"),
    ({"values": [(1, 0, 0, .5)] * 8}, "handedness"),
])
def test_malformed_tangent_fails_and_preserves_package(tmp_path, capsys, mutation, message):
    source = _source(tmp_path)
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package", "--collision", "box"]) == 0
    before = _snapshot(output)
    mutation = dict(mutation)
    values = mutation.pop("values", [(1, 0, 0, 1)] * 8)
    _add_tangents(source, values, **mutation)
    assert main([str(source), str(output), "--package", "--collision", "box"]) == 2
    assert message.lower() in capsys.readouterr().err.lower()
    assert _snapshot(output) == before


def test_tangent_without_normal_is_ignored(tmp_path):
    source = _source(tmp_path)
    _add_tangents(source, [(float("nan"), 0, 0, .5)] * 8, remove_normal=True)
    assert main([str(source), str(tmp_path / "out.nif")]) == 0


def test_tangent_preservation_is_explicit_opt_in_and_changes_nif_bytes(tmp_path):
    first = _source(tmp_path)
    second = tmp_path / "second.gltf"
    second.write_bytes(first.read_bytes())
    second_bin = tmp_path / "second.bin"
    original_bin = first.with_suffix(".bin")
    second_bin.write_bytes(original_bin.read_bytes())
    doc = json.loads(second.read_text())
    doc["buffers"][0]["uri"] = second_bin.name
    second.write_text(json.dumps(doc))
    _add_tangents(first, [(1, 0, 0, 1)] * 8)
    _add_tangents(second, [(0, 0, 1, 1)] * 8)

    bare_a, bare_b = read_gltf(str(first)), read_gltf(str(second))
    assert not bare_a[0].tangents and not bare_b[0].tangents
    bytes_a = build_nif(bare_a, "textures\\test", [False])
    bytes_b = build_nif(bare_b, "textures\\test", [False])
    assert bytes_a == bytes_b

    kept_a = read_gltf(str(first), preserve_tangents=True)
    kept_b = read_gltf(str(second), preserve_tangents=True)
    assert kept_a[0].tangents != kept_b[0].tangents
    assert build_nif(kept_a, "textures\\test", [False]) != build_nif(
        kept_b, "textures\\test", [False])


def test_large_split_preserves_every_supplied_tangent_in_real_nif():
    count = 65_538
    tangents = [(1.0, 0.0, 0.0, -1.0 if (i // 3) % 2 else 1.0) for i in range(count)]
    mesh = Mesh(
        name="large", positions=[(float(i), float(i % 2), 0.0) for i in range(count)],
        normals=[(0.0, 0.0, 1.0)] * count,
        uvs=[(float(i % 3 == 1), float(i % 3 == 2)) for i in range(count)],
        triangles=[(i, i + 1, i + 2) for i in range(0, count, 3)],
        tangents=tangents)
    parts = split_meshes([mesh])
    assert [len(part.positions) for part in parts] == [65_535, 3]
    assert [part.tangents for part in parts] == [tangents[:65_535], tangents[65_535:]]
    frames = _frames(build_nif(parts, "textures\\split", [False, False]))
    assert [len(frame) for frame in frames] == [65_535, 3]
    expected_t = np.array([1.0, 0.0, 0.0])
    for frame, source in zip(frames, (tangents[:65_535], tangents[65_535:])):
        np.testing.assert_allclose(frame[:, :, 0],
                                   np.tile(expected_t, (len(frame), 1)), atol=1 / 127)
        expected_b = np.array([[0.0, sign, 0.0] for *_, sign in source])
        np.testing.assert_allclose(frame[:, :, 1], expected_b, atol=1 / 127)


def test_axis_rotation_and_negative_scale_update_tangent_xyz_and_handedness():
    mesh = Mesh(positions=[(0, 0, 0)] * 3, normals=[(0, 0, 1)] * 3,
                uvs=[(0, 0)] * 3, triangles=[(0, 1, 2)],
                tangents=[(0, 1, 0, 1)] * 3)
    apply([mesh], scale=-2, up_axis="z")
    assert mesh.tangents == [(0, 0, 1, -1)] * 3


def test_atlas_ignores_supplied_tangent_without_source_normals(tmp_path):
    from tests.test_package_bake import _write_two_uv_gltf
    from tests.test_package_normal_frames import _add_tangents_and_transform

    source = tmp_path / "mixed.gltf"
    _write_two_uv_gltf(source)
    _add_tangents_and_transform(source, {})
    doc = json.loads(source.read_text())
    attrs = doc["meshes"][0]["primitives"][0]["attributes"]
    del attrs["NORMAL"]
    # Deliberately disagree with UV1's generated -Y tangent, so using this
    # otherwise valid authored frame would change the normal DDS.
    accessor = doc["accessors"][attrs["TANGENT"]]
    view = doc["bufferViews"][accessor["bufferView"]]
    raw = bytearray(base64.b64decode(doc["buffers"][0]["uri"].split(",", 1)[1]))
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    raw[start:start + 64] = np.tile(np.array([1, 0, 0, -1], dtype="<f4"), (4, 1)).tobytes()
    doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    source.write_text(json.dumps(doc))
    output = tmp_path / "Data"
    args = [str(source), str(output), "--package", "--bake-size", "64", "--collision", "none"]
    assert main(args) == 0
    before = _snapshot(output)
    del attrs["TANGENT"]
    source.write_text(json.dumps(doc))
    assert main(args) == 0
    assert _snapshot(output) == before
