import numpy as np
import pytest

from any2nif.texture_sampling import (
    CLAMP_TO_EDGE,
    MIRRORED_REPEAT,
    REPEAT,
    sample_rgba,
)


@pytest.fixture
def corners():
    return np.array(
        [
            [[255, 0, 0, 255], [0, 255, 0, 128]],
            [[0, 0, 255, 64], [255, 255, 255, 0]],
        ],
        dtype=np.uint8,
    )


def test_nearest_uses_gltf_top_left_image_origin(corners):
    result = sample_rgba(
        corners,
        np.array([[0.25, 0.25], [0.75, 0.25], [0.25, 0.75]]),
        linear=False,
    )
    np.testing.assert_allclose(result, corners[[0, 0, 1], [0, 1, 0]] / 255.0)


@pytest.mark.parametrize(
    ("mode", "u", "expected_red"),
    [
        (REPEAT, 1.25, 1.0),
        (REPEAT, -0.25, 0.0),
        (MIRRORED_REPEAT, 1.25, 0.0),
        (MIRRORED_REPEAT, -0.25, 1.0),
        (CLAMP_TO_EDGE, 1.25, 0.0),
        (CLAMP_TO_EDGE, -0.25, 1.0),
    ],
)
def test_nearest_wrap_s(mode, u, expected_red):
    image = np.array([[[255, 0, 0, 255], [0, 0, 0, 255]]], dtype=np.uint8)
    assert sample_rgba(image, np.array([[u, 0.5]]), wrap_s=mode, linear=False)[0, 0] == expected_red


def test_wrap_t_is_independent(corners):
    repeated = sample_rgba(corners, np.array([[0.25, 1.25]]), wrap_t=REPEAT, linear=False)
    mirrored = sample_rgba(corners, np.array([[0.25, 1.25]]), wrap_t=MIRRORED_REPEAT, linear=False)
    clamped = sample_rgba(corners, np.array([[0.25, -0.25]]), wrap_t=CLAMP_TO_EDGE, linear=False)
    np.testing.assert_allclose(repeated[0], corners[0, 0] / 255.0)
    np.testing.assert_allclose(mirrored[0], corners[1, 0] / 255.0)
    np.testing.assert_allclose(clamped[0], corners[0, 0] / 255.0)


def test_bilinear_uses_texel_centers_and_wraps_each_index():
    image = np.array([[[255, 0, 0, 255], [0, 0, 0, 255]]], dtype=np.uint8)
    center = sample_rgba(image, np.array([[0.5, 0.5]]), linear=True)
    repeat_edge = sample_rgba(image, np.array([[0.0, 0.5]]), wrap_s=REPEAT)
    clamp_edge = sample_rgba(image, np.array([[0.0, 0.5]]), wrap_s=CLAMP_TO_EDGE)
    mirror_edge = sample_rgba(image, np.array([[1.0, 0.5]]), wrap_s=MIRRORED_REPEAT)
    np.testing.assert_allclose(center[0], [0.5, 0.0, 0.0, 1.0])
    np.testing.assert_allclose(repeat_edge[0], [0.5, 0.0, 0.0, 1.0])
    np.testing.assert_allclose(clamp_edge[0], [1.0, 0.0, 0.0, 1.0])
    np.testing.assert_allclose(mirror_edge[0], [0.0, 0.0, 0.0, 1.0])


def test_srgb_rgb_is_decoded_before_interpolation_but_alpha_is_linear():
    image = np.array([[[0, 0, 0, 0], [255, 255, 255, 255]]], dtype=np.uint8)
    encoded = sample_rgba(image, np.array([[0.5, 0.5]]), srgb=False)
    linear = sample_rgba(image, np.array([[0.5, 0.5]]), srgb=True)
    np.testing.assert_allclose(encoded[0], [0.5, 0.5, 0.5, 0.5])
    np.testing.assert_allclose(linear[0], [0.5, 0.5, 0.5, 0.5])

    midgray = np.array([[[128, 128, 128, 128]]], dtype=np.uint8)
    decoded = sample_rgba(midgray, np.array([[0.5, 0.5]]), srgb=True)
    expected_rgb = ((128 / 255 + 0.055) / 1.055) ** 2.4
    np.testing.assert_allclose(decoded[0, :3], expected_rgb)
    np.testing.assert_allclose(decoded[0, 3], 128 / 255)


@pytest.mark.parametrize("name", ["wrap_s", "wrap_t"])
def test_invalid_wrap_is_rejected(corners, name):
    with pytest.raises(ValueError, match=f"invalid {name}"):
        sample_rgba(corners, np.array([[0.5, 0.5]]), **{name: 12345})


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_non_finite_uv_is_rejected(corners, bad):
    with pytest.raises(ValueError, match="finite"):
        sample_rgba(corners, np.array([[bad, 0.5]]))


@pytest.mark.parametrize(
    "rgba",
    [
        np.zeros((2, 2, 3), dtype=np.uint8),
        np.zeros((2, 2, 4), dtype=np.float32),
        np.zeros((0, 2, 4), dtype=np.uint8),
    ],
)
def test_invalid_image_is_rejected(rgba):
    with pytest.raises(ValueError, match="rgba"):
        sample_rgba(rgba, np.empty((0, 2)))


def test_empty_uv_returns_empty_result(corners):
    result = sample_rgba(corners, np.empty((0, 2)))
    assert result.shape == (0, 4)
