"""Expand scene instances for baking in the same space as the output mesh."""

import copy

import numpy as np
from pygltflib import Node, Scene

from gltf2nif.gltf_reader import _read_accessor, _scene_instances, _transform_geometry
from gltf2nif.tangents import read_tangents, tangent_basis


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
            tangents = read_tangents(gltf, buffers, attrs, len(positions), world[:3, :3])
            if tangents:
                tangent_basis(tangents, normals)
                attrs.TANGENT = append_accessor(gltf, buffers, tangents, 'VEC4')
            else:
                attrs.TANGENT = None
            if determinant < 0:
                indices = (_read_accessor(gltf, buffers, primitive.indices) if primitive.indices is not None
                           else np.arange(len(positions)))
                triangles = np.asarray(indices).reshape(-1, 3)[:, [0, 2, 1]]
                primitive.indices = append_accessor(gltf, buffers, triangles.reshape(-1), 'SCALAR', indices=True)
        nodes.append(Node(name=node.name or original.name, mesh=len(meshes)))
        meshes.append(mesh)
    gltf.meshes, gltf.nodes, gltf.scenes, gltf.scene = meshes, nodes, [Scene(nodes=list(range(len(nodes))))], 0
