"""Builders for synthetic glTF inputs to exercise gltf2nif offline.

Emits real .gltf + .bin pairs (interleaved vertex buffer, material name carrying a
texture base name) so gltf_reader and the writer round-trip run against genuine glTF,
not a mock.
"""

from __future__ import annotations

import numpy as np
from pygltflib import (
    ARRAY_BUFFER,
    ELEMENT_ARRAY_BUFFER,
    FLOAT,
    GLTF2,
    SCALAR,
    UNSIGNED_INT,
    VEC2,
    VEC3,
    Accessor,
    Attributes,
    Buffer,
    BufferView,
    Material,
    Node,
    Primitive,
    Scene,
)
from pygltflib import Mesh as GltfMesh


def write_gltf_interleaved(path, prims):
    """prims: list of dicts {positions, normals, uvs, triangles, material}.
    Positions/normals float3, uvs float2, interleaved in one bufferView (stride 32)."""
    blob = bytearray()
    bufferViews = []
    accessors = []
    meshes = []
    nodes = []
    materials = []

    for pi, p in enumerate(prims):
        pos = np.asarray(p["positions"], dtype=np.float32)
        nrm = np.asarray(p["normals"], dtype=np.float32)
        uv = np.asarray(p["uvs"], dtype=np.float32)
        n = len(pos)
        inter = np.zeros((n, 8), dtype=np.float32)
        inter[:, 0:3] = pos
        inter[:, 3:6] = nrm
        inter[:, 6:8] = uv
        vbytes = inter.tobytes()
        voff = len(blob)
        blob.extend(vbytes)
        vbv = len(bufferViews)
        bufferViews.append(BufferView(buffer=0, byteOffset=voff, byteLength=len(vbytes),
                                      byteStride=32, target=ARRAY_BUFFER))
        pos_acc = len(accessors)
        accessors.append(Accessor(bufferView=vbv, byteOffset=0, componentType=FLOAT,
                                  count=n, type=VEC3,
                                  min=pos.min(axis=0).tolist(), max=pos.max(axis=0).tolist()))
        nrm_acc = len(accessors)
        accessors.append(Accessor(bufferView=vbv, byteOffset=12, componentType=FLOAT,
                                  count=n, type=VEC3))
        uv_acc = len(accessors)
        accessors.append(Accessor(bufferView=vbv, byteOffset=24, componentType=FLOAT,
                                  count=n, type=VEC2))
        idx = np.asarray(p["triangles"], dtype=np.uint32).reshape(-1)
        ioff = len(blob)
        blob.extend(idx.tobytes())
        ibv = len(bufferViews)
        bufferViews.append(BufferView(buffer=0, byteOffset=ioff, byteLength=idx.nbytes,
                                      target=ELEMENT_ARRAY_BUFFER))
        idx_acc = len(accessors)
        accessors.append(Accessor(bufferView=ibv, componentType=UNSIGNED_INT,
                                  count=len(idx), type=SCALAR))
        mat_i = len(materials)
        materials.append(Material(name=p.get("material", "")))
        prim = Primitive(attributes=Attributes(POSITION=pos_acc, NORMAL=nrm_acc,
                                               TEXCOORD_0=uv_acc), indices=idx_acc, material=mat_i)
        meshes.append(GltfMesh(primitives=[prim], name=f"mesh{pi}"))
        nodes.append(Node(mesh=pi))

    gltf = GLTF2(scene=0, scenes=[Scene(nodes=list(range(len(nodes))))], nodes=nodes,
                 meshes=meshes, materials=materials, accessors=accessors,
                 bufferViews=bufferViews,
                 buffers=[Buffer(byteLength=len(blob), uri=path.rsplit("/", 1)[-1][:-5] + ".bin")])
    import os
    bin_path = path[:-5] + ".bin"
    with open(bin_path, "wb") as fh:
        fh.write(bytes(blob))
    gltf.save_json(path)
    return path


CUBE_POS = [
    (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
    (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
]
CUBE_TRIS = [
    (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
    (0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6),
    (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3),
]
