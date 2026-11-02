"""Validate real converter assets, collision links, and Mutagen catalog readback."""

import json
import struct

import numpy as np
from pygltflib import GLTF2

from any2nif.package import _validate, texture_references
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif
from .safety import hashes, relative_path


def embedded_glb(source):
    if source.suffix.lower() != ".glb":
        raise ValueError("this single-model slice accepts embedded GLB only")
    doc = GLTF2().load_binary(str(source))
    for item in [*doc.buffers, *doc.images]:
        uri = getattr(item, "uri", None)
        if uri and not uri.startswith("data:"):
            raise ValueError("GLB external resource URIs are not permitted; embed buffers/images")


def box_collision(data):
    header = _read_header(_Reader(data))
    kinds, offsets = header["types"], header["offsets"]

    def block(index, kind):
        if index < 0 or index >= len(kinds) or kinds[index] != kind:
            raise ValueError(f"invalid collision link: expected {kind}, index={index}")
        return offsets[index]

    root = block(0, "BSFadeNode")
    extra_count = struct.unpack_from("<I", data, root + 4)[0]
    collision_index = struct.unpack_from("<i", data, root + 68 + extra_count * 4)[0]
    collision = block(collision_index, "bhkCollisionObject")
    if struct.unpack_from("<i", data, collision)[0] != 0:
        raise ValueError("collision does not target the render root")
    body = block(struct.unpack_from("<i", data, collision + 6)[0], "bhkRigidBody")
    shape = block(struct.unpack_from("<i", data, body)[0], "bhkConvexVerticesShape")
    count = struct.unpack_from("<I", data, shape + 32)[0]
    if count != 8:
        raise ValueError("box collision needs eight corners")
    points = np.array([struct.unpack_from("<4f", data, shape + 36 + i * 16)[:3]
                       for i in range(count)])
    render = np.array([mesh.positions[index] for mesh in read_nif(data)
                       for index in sorted({i for tri in mesh.triangles for i in tri})]) / 70.03
    render = render[:, [0, 2, 1]].copy()
    render[:, 1] *= -1
    low, high = points.min(axis=0), points.max(axis=0)
    expected_low, expected_high = render.min(axis=0), render.max(axis=0)
    padding = np.maximum(0, 0.05 - (expected_high - expected_low)) / 2
    if (not np.isfinite(points).all() or np.any(high <= low) or
            len(set(map(tuple, points))) != 8 or
            any(not np.all(np.isclose(p, low) | np.isclose(p, high)) for p in points) or
            not np.allclose(low, expected_low - padding, atol=1e-5) or
            not np.allclose(high, expected_high + padding, atol=1e-5)):
        raise ValueError("box collision corners/bounds disagree with the render mesh")
    return {"corners": count, "min": low.tolist(), "max": high.tolist()}


def converter_assets(directory, name):
    marker = json.loads((directory / "converter-package.json").read_text(encoding="utf-8"))
    if not isinstance(marker, dict):
        raise ValueError("converter manifest must be an object")
    mesh = relative_path(marker["mesh"], "meshes")
    if (marker.get("schema") != "model-converter-package/1" or
            mesh != f"meshes/any2nif/{name}/{name}.nif" or marker.get("collision") != "box"):
        raise ValueError("unexpected converter package schema/mesh/collision")
    actual = hashes(directory, ("converter-package.json",))
    for path in actual:
        folder = path.split("/")[0]
        if folder not in ("meshes", "textures"):
            raise ValueError(f"non-permitted Data folder: {folder}")
    if actual != marker.get("sha256"):
        raise ValueError("converter manifest does not cover exact asset bytes")
    refs = texture_references((directory / mesh).read_bytes())
    if refs != marker.get("textures"):
        raise ValueError("converter manifest texture references disagree with NIF")
    for ref in refs:
        relative_path(ref, "textures")
    _validate(directory, directory / mesh)
    collision = box_collision((directory / mesh).read_bytes())
    return marker, collision


def static_readback(document, editor_id, model, plugin):
    sources, records = document["sources"], document["records"]
    if len(sources) != 1 or sources[0]["recordCount"] != 1 or len(records) != 1:
        raise ValueError("package must contain exactly one major record")
    record = records[0]
    if (record["recordType"] != "Static" or record["editorId"] != editor_id or
            record["plugin"].lower() != plugin.lower() or
            relative_path(record["modelPath"]).lower() != model.lower()):
        raise ValueError("ESP readback differs from the single StaticSpec")
    # MODL is relative to the permitted model Data folder, Meshes. NIF texture
    # references instead already include their permitted Textures folder.
    if model.split("/")[0].lower() in ("meshes", "textures", "data"):
        raise ValueError("Static.Model must be Meshes-root-relative")
    return {key: record[key] for key in ("formKey", "recordType", "editorId", "modelPath")}
