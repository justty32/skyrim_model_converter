"""Write a list of `Mesh` (glTF space) to a glTF 2.0 file (.gltf + .bin).

MVP: one flat StandardMaterial (no texture references), POSITION + optional
NORMAL / TEXCOORD_0, UNSIGNED_INT indices. Geometry is assumed already in
glTF space (Y-up) — see geometry.skyrim_to_gltf_*.
"""

from __future__ import annotations

import os

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
    PbrMetallicRoughness,
    Primitive,
    Scene,
)
from pygltflib import Mesh as GltfMesh

from .geometry import Mesh


def _pad4(blob: bytearray) -> None:
    while len(blob) % 4 != 0:
        blob.append(0)


def write_gltf(
    meshes: list[Mesh],
    out_path: str,
    flat_color: tuple[float, float, float, float] = (0.6, 0.6, 0.6, 1.0),
    material_name: str = "flat_proxy",
) -> str:
    """Serialise `meshes` as a flat proxy to `out_path` plus a sibling .bin."""
    out_dir = os.path.dirname(os.path.abspath(out_path))
    stem = os.path.splitext(os.path.basename(out_path))[0]
    bin_name = stem + ".bin"

    blob = bytearray()
    buffer_views: list[BufferView] = []
    accessors: list[Accessor] = []

    def add_buffer_view(data: bytes, target: int) -> int:
        _pad4(blob)
        offset = len(blob)
        blob.extend(data)
        buffer_views.append(
            BufferView(buffer=0, byteOffset=offset, byteLength=len(data), target=target)
        )
        return len(buffer_views) - 1

    def add_accessor(
        bv: int, comp_type: int, count: int, acc_type: str,
        mins=None, maxs=None,
    ) -> int:
        accessors.append(
            Accessor(
                bufferView=bv,
                componentType=comp_type,
                count=count,
                type=acc_type,
                min=mins,
                max=maxs,
            )
        )
        return len(accessors) - 1

    gltf_meshes: list[GltfMesh] = []
    nodes: list[Node] = []

    materials: list[Material] = [Material(
        name=material_name,
        pbrMetallicRoughness=PbrMetallicRoughness(
            baseColorFactor=list(flat_color), metallicFactor=0.0, roughnessFactor=1.0),
        doubleSided=True,
    )]
    for i, mesh in enumerate(meshes):
        if mesh.is_empty():
            continue

        positions = np.asarray(mesh.positions, dtype=np.float32).reshape(-1, 3)
        tris = np.asarray(mesh.triangles, dtype=np.uint32).reshape(-1)

        pos_bv = add_buffer_view(positions.tobytes(), ARRAY_BUFFER)
        pos_acc = add_accessor(
            pos_bv, FLOAT, len(positions), VEC3,
            mins=positions.min(axis=0).tolist(),
            maxs=positions.max(axis=0).tolist(),
        )

        attrs = Attributes(POSITION=pos_acc)

        if mesh.has_normals:
            normals = np.asarray(mesh.normals, dtype=np.float32).reshape(-1, 3)
            nrm_bv = add_buffer_view(normals.tobytes(), ARRAY_BUFFER)
            attrs.NORMAL = add_accessor(nrm_bv, FLOAT, len(normals), VEC3)

        if mesh.has_uvs:
            uvs = np.asarray(mesh.uvs, dtype=np.float32).reshape(-1, 2)
            uv_bv = add_buffer_view(uvs.tobytes(), ARRAY_BUFFER)
            attrs.TEXCOORD_0 = add_accessor(uv_bv, FLOAT, len(uvs), VEC2)

        idx_bv = add_buffer_view(tris.tobytes(), ELEMENT_ARRAY_BUFFER)
        idx_acc = add_accessor(idx_bv, UNSIGNED_INT, len(tris), SCALAR)

        prim = Primitive(attributes=attrs, indices=idx_acc, material=0)
        gltf_meshes.append(GltfMesh(primitives=[prim], name=mesh.name or f"shape_{i}"))
        nodes.append(Node(mesh=len(gltf_meshes) - 1, name=mesh.name or f"shape_{i}"))

    gltf = GLTF2(
        scene=0,
        scenes=[Scene(nodes=list(range(len(nodes))))],
        nodes=nodes,
        meshes=gltf_meshes,
        materials=materials,
        accessors=accessors,
        bufferViews=buffer_views,
        buffers=[Buffer(byteLength=len(blob), uri=bin_name)],
    )

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, bin_name), "wb") as fh:
        fh.write(bytes(blob))
    gltf.save_json(out_path)
    # A flat conversion must not leave texture metadata from an earlier run at the same path.
    sidecar_path = os.path.splitext(out_path)[0] + ".textures.json"
    if os.path.isfile(sidecar_path):
        os.remove(sidecar_path)
    return out_path
