import numpy as np
import pytest

from any2nif.uv_atlas import (
    dilate_pixels, ensure_face_coverage, generate_atlas, rasterize,
)


def test_generate_atlas_preserves_source_triangle_corners():
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], np.float32)
    triangles = np.array([[0, 1, 2], [0, 2, 3]], np.uint32)
    normals = np.tile([0, 0, 1], (4, 1)).astype(np.float32)
    vmapping, output, uvs = generate_atlas(positions, triangles, normals, 64)
    np.testing.assert_array_equal(vmapping[output], triangles)
    assert output.shape == triangles.shape
    assert uvs.shape == (len(vmapping), 2)
    assert np.all((uvs >= 0) & (uvs <= 1))
    ids, _ = rasterize(uvs, output, 64)
    assert set(np.unique(ids)) >= {0, 1}


def test_rasterize_uses_texel_centers_and_v_zero_is_top():
    uvs = np.array([[0, 0], [1, 0], [0, 1]], np.float32)
    ids, bary = rasterize(uvs, np.array([[0, 1, 2]]), 2)
    np.testing.assert_array_equal(ids, [[0, 0], [0, -1]])
    np.testing.assert_allclose(bary[0, 0], [.5, .25, .25])
    np.testing.assert_allclose(bary[0, 1], [0, .75, .25])
    np.testing.assert_allclose(bary[1, 0], [0, .25, .75])
    np.testing.assert_array_equal(bary[1, 1], [0, 0, 0])


def test_shared_edge_is_deterministic_but_interior_overlap_is_rejected():
    square = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float64)
    ids, _ = rasterize(square, [[0, 1, 2], [0, 2, 3]], 4)
    assert np.count_nonzero(ids == 0) + np.count_nonzero(ids == 1) == 16
    with pytest.raises(ValueError, match="overlap"):
        rasterize([[0, 0], [1, 0], [0, 1]], [[0, 1, 2], [0, 1, 2]], 4)


def test_zero_area_and_bad_data_are_rejected():
    with pytest.raises(ValueError, match="zero UV area"):
        rasterize([[0, 0], [.5, 0], [1, 0]], [[0, 1, 2]], 4)
    with pytest.raises(ValueError, match="finite"):
        rasterize([[0, 0], [1, 0], [0, np.nan]], [[0, 1, 2]], 4)
    with pytest.raises(ValueError, match="out of range"):
        rasterize([[0, 0], [1, 0], [0, 1]], [[0, 1, 3]], 4)


def test_quantized_mode_skips_collapsed_faces_and_resolves_collisions():
    with pytest.warns(RuntimeWarning, match=r"1 UV triangle\(s\)"):
        ids, _ = rasterize([[0, 0], [0, 0], [0, 0]], [[0, 1, 2]], 4,
                           allow_degenerate=True, quantized=True,
                           reference_uvs=[[0, 0], [0, 0], [0, 0]])
    assert np.all(ids == -1)


def test_quantized_mode_requires_a_valid_nonoverlapping_reference():
    with pytest.raises(ValueError, match="requires reference_uvs"):
        rasterize([[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], 4, quantized=True)
    with pytest.raises(ValueError, match="overlap"):
        rasterize([[0, 0], [1, 0], [0, 1]], [[0, 1, 2], [0, 1, 2]], 4,
                  quantized=True,
                  reference_uvs=[[0, 0], [1, 0], [0, 1]])


def test_tiny_triangle_warns_when_it_has_no_texel_center():
    with pytest.warns(RuntimeWarning, match=r"1 UV triangle\(s\).*first face ids: 0"):
        ids, bary = rasterize([[0, 0], [.01, 0], [0, .01]], [[0, 1, 2]], 8)
    assert np.all(ids == -1)
    assert not bary.any()


def test_dilate_pixels_copies_nearest_covered_texel_only_within_padding():
    pixels = np.zeros((5, 5, 3), np.float32)
    pixels[2, 2] = [1, .5, .25]
    ids = np.full((5, 5), -1, np.int32)
    ids[2, 2] = 7
    result = dilate_pixels(pixels, ids, padding=1)
    np.testing.assert_array_equal(result[1, 2], pixels[2, 2])
    np.testing.assert_array_equal(result[2, 1], pixels[2, 2])
    np.testing.assert_array_equal(result[1, 1], [0, 0, 0])
    np.testing.assert_array_equal(result[0, 2], [0, 0, 0])
    np.testing.assert_array_equal(ids[1, 2], -1)


@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
def test_bad_size_is_rejected(bad):
    with pytest.raises(ValueError, match="positive integer"):
        rasterize([[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], bad)


def test_empty_atlas_input_is_rejected_before_backend_call():
    with pytest.raises(ValueError, match="at least one vertex and triangle"):
        generate_atlas(np.empty((0, 3)), np.empty((0, 3), dtype=np.int64),
                       np.empty((0, 3)), 64)


def test_coverage_fallback_preserves_corners_and_gives_tiny_face_own_texels():
    mapping = np.arange(6, dtype=np.uint32)
    triangles = np.array([[0, 1, 2], [3, 4, 5]], np.uint32)
    raw = np.array([[0, 0], [.45, 0], [0, .45],
                    [.9, .9], [.900001, .9], [.9, .900001]], np.float32)
    with pytest.warns(RuntimeWarning, match="independent fallback tiles"):
        new_map, new_tri, half, owners, _ = ensure_face_coverage(
            mapping, triangles, raw, 64)
    np.testing.assert_array_equal(new_map[new_tri], mapping[triangles])
    assert len(new_map) == len(mapping) + 3
    assert set(np.unique(owners)) >= {-1, 0, 1}
    assert np.count_nonzero(owners == 1) > 0
    assert not np.shares_memory(new_tri[0], new_tri[1])


def test_coverage_fallback_repairs_half_precision_collapse():
    raw = np.array([[.5, .5], [.50001, .5], [.5, .50001]], np.float32)
    assert np.unique(raw.astype(np.float16), axis=0).shape[0] == 1
    with pytest.warns(RuntimeWarning, match="placed 1 face"):
        mapping, triangles, half, owners, _ = ensure_face_coverage(
            np.arange(3), [[0, 1, 2]], raw, 64)
    assert len(mapping) == 6
    assert np.count_nonzero(owners == 0) > 0
    a = half[triangles[0, 1]] - half[triangles[0, 0]]
    b = half[triangles[0, 2]] - half[triangles[0, 0]]
    assert abs(a[0] * b[1] - a[1] * b[0]) > 0


def test_coverage_fallback_reports_when_no_safe_tile_fits():
    raw = np.array([[0, 0], [1, 0], [0, 1], [.9, .9], [.90001, .9], [.9, .90001]])
    with pytest.raises(ValueError, match="increase --bake-size"):
        ensure_face_coverage(np.arange(6), [[0, 1, 2], [3, 4, 5]], raw, 8)
