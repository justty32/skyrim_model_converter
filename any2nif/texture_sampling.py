"""Small, vectorized glTF texture sampler used by texture baking.

glTF sampler and image-coordinate semantics are specified at:
https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#textures
"""

from __future__ import annotations

import numpy as np


REPEAT = 10497
MIRRORED_REPEAT = 33648
CLAMP_TO_EDGE = 33071
_WRAP_MODES = {REPEAT, MIRRORED_REPEAT, CLAMP_TO_EDGE}


def _wrapped_indices(indices: np.ndarray, size: int, mode: int) -> np.ndarray:
    if mode == CLAMP_TO_EDGE:
        return np.clip(indices, 0, size - 1)
    if mode == REPEAT:
        return np.mod(indices, size)

    mirrored = np.mod(indices, 2 * size)
    return np.where(mirrored < size, mirrored, 2 * size - 1 - mirrored)


def _srgb_decode(values: np.ndarray) -> np.ndarray:
    return np.where(
        values <= 0.04045,
        values / 12.92,
        ((values + 0.055) / 1.055) ** 2.4,
    )


def sample_rgba(
    rgba: np.ndarray,
    uv: np.ndarray,
    wrap_s: int = REPEAT,
    wrap_t: int = REPEAT,
    linear: bool = True,
    srgb: bool = False,
) -> np.ndarray:
    """Sample an HxWx4 uint8 image at glTF UVs, returning Nx4 floats.

    The image is kept in glTF/Pillow row order: ``v=0`` addresses its top row.
    With ``srgb=True``, RGB is decoded to linear light before filtering; alpha is
    always treated as linear.  Returned RGB remains linear in that mode.
    """
    image = np.asarray(rgba)
    coordinates = np.asarray(uv)
    if image.ndim != 3 or image.shape[2] != 4 or image.dtype != np.uint8:
        raise ValueError("rgba must be a uint8 array with shape (H, W, 4)")
    height, width, _ = image.shape
    if height == 0 or width == 0:
        raise ValueError("rgba dimensions must be non-zero")
    if coordinates.ndim != 2 or coordinates.shape[1] != 2:
        raise ValueError("uv must have shape (N, 2)")
    if not np.issubdtype(coordinates.dtype, np.number):
        raise ValueError("uv must contain numeric values")
    coordinates = coordinates.astype(np.float64, copy=False)
    if not np.all(np.isfinite(coordinates)):
        raise ValueError("uv must contain only finite values")
    if wrap_s not in _WRAP_MODES:
        raise ValueError(f"invalid wrap_s: {wrap_s}")
    if wrap_t not in _WRAP_MODES:
        raise ValueError(f"invalid wrap_t: {wrap_t}")

    def pixels_at(yi, xi):
        pixels = image[yi, xi].astype(np.float64) / 255.0
        if srgb:
            pixels[..., :3] = _srgb_decode(pixels[..., :3])
        return pixels

    x = coordinates[:, 0] * width - 0.5
    y = coordinates[:, 1] * height - 0.5
    if not linear:
        xi = _wrapped_indices(np.floor(x + 0.5).astype(np.int64), width, wrap_s)
        yi = _wrapped_indices(np.floor(y + 0.5).astype(np.int64), height, wrap_t)
        return pixels_at(yi, xi)

    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    tx = (x - x0)[:, None]
    ty = (y - y0)[:, None]
    x1 = x0 + 1
    y1 = y0 + 1
    x0 = _wrapped_indices(x0, width, wrap_s)
    x1 = _wrapped_indices(x1, width, wrap_s)
    y0 = _wrapped_indices(y0, height, wrap_t)
    y1 = _wrapped_indices(y1, height, wrap_t)

    top = pixels_at(y0, x0) * (1.0 - tx) + pixels_at(y0, x1) * tx
    bottom = pixels_at(y1, x0) * (1.0 - tx) + pixels_at(y1, x1) * tx
    return top * (1.0 - ty) + bottom * ty
