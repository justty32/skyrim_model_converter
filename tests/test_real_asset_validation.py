import numpy as np

from tools.smoke_real_assets import _corner_geometry


class _Mesh:
    def __init__(self, positions, normals, uvs, triangles):
        self.positions = positions
        self.normals = normals
        self.uvs = uvs
        self.triangles = triangles


def test_corner_validation_accepts_atlas_vertex_duplication():
    before = _Mesh(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [(0, 0, 1)] * 4, [(0, 0)] * 4, [(0, 1, 2), (0, 2, 3)])
    # The second triangle has independent seam vertices and arbitrary atlas UVs.
    positions = np.asarray([(0, 0, 0), (1, 0, 0), (1, 1, 0),
                            (0, 0, 0), (1, 1, 0), (0, 1, 0)]) * 70.03
    after = _Mesh(positions, [(0, 0, 1)] * 6,
                  [(0, 0), (.4, 0), (.4, .4), (.6, .6), (1, .6), (1, 1)],
                  [(0, 1, 2), (3, 4, 5)])
    _corner_geometry(before, after, atlas=True)
