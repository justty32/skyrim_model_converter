"""Expand scene instances for baking in the same space as the output mesh."""

import copy

import numpy as np
from pygltflib import Node, Scene

from gltf2nif.gltf_reader import _read_accessor, _scene_instances, _transform_geometry
from gltf2nif import GltfError


def flatten_instances(gltf, buffers):
    from .package_bake import append_accessor

    instances = list(_scene_instances(gltf))
    meshes, nodes = [], []
    for node, mesh_index, world in instances:
        original = gltf.meshes[mesh_index]
        mesh = copy.deepcopy(original)
        mesh.primitives = [p for p in mesh.primitives
                           if p.mode in (None, 4) and getattr(p.attributes, "POSITION", None) is not None
                           and gltf.accessors[p.attributes.POSITION].count
                           and (p.indices is None or gltf.accessors[p.indices].count)]
        for primitive in mesh.primitives:
            attrs = primitive.attributes
            positions = _read_accessor(gltf, buffers, attrs.POSITION)
            normals = (_read_accessor(gltf, buffers, attrs.NORMAL) if attrs.NORMAL is not None else [])
            positions, normals = _transform_geometry(positions, normals, world)
            attrs.POSITION = append_accessor(gltf, buffers, positions, 'VEC3')
            if normals:
                attrs.NORMAL = append_accessor(gltf, buffers, normals, 'VEC3')
            determinant = float(np.linalg.det(world[:3, :3]))
            if attrs.TANGENT is not None:
                acc = gltf.accessors[attrs.TANGENT]
                if acc.type != 'VEC4' or acc.componentType != 5126 or acc.normalized:
                    raise GltfError('TANGENT must use non-normalized FLOAT VEC4')
                tangents = np.asarray(_read_accessor(gltf, buffers, attrs.TANGENT), dtype=np.float64)
                if tangents.shape != (len(positions), 4) or not np.isfinite(tangents).all():
                    raise GltfError('TANGENT has invalid count or values')
                tangents[:, :3] = tangents[:, :3] @ world[:3, :3].T
                lengths = np.linalg.norm(tangents[:, :3], axis=1)
                if np.any(lengths < 1e-12):
                    raise GltfError('node transform produced a zero-length tangent')
                tangents[:, :3] /= lengths[:, None]
                tangents[:, 3] *= -1 if determinant < 0 else 1
                attrs.TANGENT = append_accessor(gltf, buffers, tangents, 'VEC4')
            if determinant < 0:
                indices = (_read_accessor(gltf, buffers, primitive.indices) if primitive.indices is not None
                           else np.arange(len(positions)))
                triangles = np.asarray(indices).reshape(-1, 3)[:, [0, 2, 1]]
                primitive.indices = append_accessor(gltf, buffers, triangles.reshape(-1), 'SCALAR', indices=True)
        nodes.append(Node(name=node.name or original.name, mesh=len(meshes)))
        meshes.append(mesh)
    gltf.meshes, gltf.nodes, gltf.scenes, gltf.scene = meshes, nodes, [Scene(nodes=list(range(len(nodes))))], 0
