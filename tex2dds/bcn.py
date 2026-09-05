"""Pure-Python (numpy) BC1 / BC3 block compressors.

No external binary is involved: everything here is plain numpy, so the encoder
runs offline and its output can be read back by any third-party BC decoder
(the test-suite cross-checks against Pillow's own DDS/bcn reader).

Layout reference (S3TC / DirectX "Block Compression"):

* **BC1** — 8 bytes per 4x4 block: ``color0`` and ``color1`` as little-endian
  RGB565, then 32 bits of 2-bit-per-texel palette indices (texel ``(x, y)`` at
  bit ``(y * 4 + x) * 2``). We always emit the **4-colour opaque mode**, which
  requires ``color0 > color1`` as unsigned 16-bit values; the palette is
  ``c0, c1, (2*c0 + c1) / 3, (c0 + 2*c1) / 3``. When a block quantises to
  ``color0 == color1`` the hardware switches to the 3-colour mode, so we then
  force every index to 0 — still exact for the flat block that causes it, and
  it never selects the transparent index 3.
* **BC4** (the alpha half of BC3) — 8 bytes per block: ``alpha0``, ``alpha1``,
  then 48 bits of 3-bit indices. With ``alpha0 > alpha1`` the palette is 8
  values interpolated between the two endpoints. Equal endpoints fall into the
  6-value mode, where index 0 still yields ``alpha0`` exactly.
* **BC3** — the BC4 alpha block followed by the BC1 colour block, 16 bytes.

Endpoint fit (see :func:`_fit_endpoints`): the RGB texels of a block are
projected onto the **principal axis** of their covariance matrix (power
iteration), the extremes of that projection give a first endpoint pair, and one
**least-squares refit** against the resulting indices sharpens them before the
final RGB565 quantisation. Flat blocks degenerate gracefully: their covariance
is zero, both endpoints collapse onto the block mean, and index 0 reproduces the
colour to within RGB565 rounding.
"""

from __future__ import annotations

import numpy as np

BC1_BLOCK_BYTES = 8
BC3_BLOCK_BYTES = 16

# Blocks handled per batch. Caps peak memory: the nearest-palette search
# materialises (chunk, 16, 4, 3) floats, so 8192 blocks is a few MB.
_CHUNK = 8192

# Weight of endpoint c0 for each of the four BC1 palette slots.
_A_WEIGHTS = np.array([1.0, 0.0, 2.0 / 3.0, 1.0 / 3.0], dtype=np.float32)


def blockify(image: np.ndarray) -> np.ndarray:
    """(H, W, C) uint8 -> (nblocks, 16, C), blocks in row-major order.

    Height and width must already be multiples of 4 (the caller pads).
    """
    if image.ndim != 3:
        raise ValueError("expected an (H, W, C) image array")
    h, w, c = image.shape
    if h % 4 or w % 4:
        raise ValueError(f"block compression needs 4-aligned dimensions, got {w}x{h}")
    grid = image.reshape(h // 4, 4, w // 4, 4, c).transpose(0, 2, 1, 3, 4)
    return np.ascontiguousarray(grid).reshape((h // 4) * (w // 4), 16, c)


def _principal_axis(pix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Block mean (n, 3) and unit principal axis (n, 3) of the colour cloud."""
    n = pix.shape[0]
    mean = pix.mean(axis=1)
    centred = pix - mean[:, None, :]
    cov = np.einsum("nkc,nkd->ncd", centred, centred)
    # Asymmetric seed so a grey-axis cloud is not orthogonal to the start vector.
    axis = np.tile(np.array([0.9, 1.0, 0.7], dtype=np.float32), (n, 1))
    for _ in range(8):
        axis = np.einsum("ncd,nd->nc", cov, axis)
        norm = np.linalg.norm(axis, axis=1, keepdims=True)
        axis = np.where(norm > 1e-8, axis / np.maximum(norm, 1e-20), 0.0)
    # Degenerate (flat) blocks: any axis works, projections collapse to zero.
    dead = np.linalg.norm(axis, axis=1) < 0.5
    axis[dead] = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    return mean.astype(np.float32), axis.astype(np.float32)


def _quantise_565(colour: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(n, 3) float RGB -> packed RGB565 (uint16) and the dequantised RGB it decodes to.

    Dequantisation uses bit replication (``r8 = r5 << 3 | r5 >> 2``), matching
    what real BC decoders do, so the encoder scores candidates against the exact
    colours the reader will produce.
    """
    c = np.clip(colour, 0.0, 255.0)
    r = np.rint(c[:, 0] * (31.0 / 255.0)).astype(np.uint16)
    g = np.rint(c[:, 1] * (63.0 / 255.0)).astype(np.uint16)
    b = np.rint(c[:, 2] * (31.0 / 255.0)).astype(np.uint16)
    packed = (r << 11) | (g << 5) | b
    deq = np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=1)
    return packed, deq.astype(np.float32)


def _bc1_palette(c0: np.ndarray, c1: np.ndarray) -> np.ndarray:
    """(n, 4, 3) four-colour opaque palette from dequantised endpoints."""
    pal = np.empty((c0.shape[0], 4, 3), dtype=np.float32)
    pal[:, 0] = c0
    pal[:, 1] = c1
    pal[:, 2] = (2.0 * c0 + c1) / 3.0
    pal[:, 3] = (c0 + 2.0 * c1) / 3.0
    return pal


def _nearest(pix: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """(n, 16) index of the closest palette entry, squared-euclidean in RGB."""
    diff = pix[:, :, None, :] - pal[:, None, :, :]
    return np.argmin(np.einsum("nkpc,nkpc->nkp", diff, diff), axis=2)


def _fit_endpoints(pix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Principal-axis range fit plus one least-squares refit. Returns float endpoints."""
    mean, axis = _principal_axis(pix)
    proj = np.einsum("nkc,nc->nk", pix - mean[:, None, :], axis)
    e0 = mean + proj.max(axis=1)[:, None] * axis
    e1 = mean + proj.min(axis=1)[:, None] * axis
    e0 = np.clip(e0, 0.0, 255.0)
    e1 = np.clip(e1, 0.0, 255.0)

    # Refit: with the indices the range fit implies, solve the 2x2 normal
    # equations for the endpoint pair that minimises squared error.
    q0, d0 = _quantise_565(e0)
    q1, d1 = _quantise_565(e1)
    swap = q0 < q1
    d0, d1 = np.where(swap[:, None], d1, d0), np.where(swap[:, None], d0, d1)
    idx = _nearest(pix, _bc1_palette(d0, d1))

    a = _A_WEIGHTS[idx]
    b = 1.0 - a
    saa = np.einsum("nk,nk->n", a, a)
    sab = np.einsum("nk,nk->n", a, b)
    sbb = np.einsum("nk,nk->n", b, b)
    sap = np.einsum("nk,nkc->nc", a, pix)
    sbp = np.einsum("nk,nkc->nc", b, pix)
    det = saa * sbb - sab * sab
    safe = np.abs(det) > 1e-6
    inv = np.where(safe, 1.0 / np.where(safe, det, 1.0), 0.0)[:, None]
    r0 = np.clip((sbb[:, None] * sap - sab[:, None] * sbp) * inv, 0.0, 255.0)
    r1 = np.clip((saa[:, None] * sbp - sab[:, None] * sap) * inv, 0.0, 255.0)
    keep = safe[:, None]
    return np.where(keep, r0, e0), np.where(keep, r1, e1)


def _encode_bc1_chunk(blocks: np.ndarray) -> np.ndarray:
    """(n, 16, >=3) uint8 -> (n, 8) uint8 BC1 colour blocks."""
    pix = blocks[:, :, :3].astype(np.float32)
    e0, e1 = _fit_endpoints(pix)
    q0, d0 = _quantise_565(e0)
    q1, d1 = _quantise_565(e1)

    # 4-colour opaque mode requires color0 > color1.
    swap = q0 < q1
    q0, q1 = np.where(swap, q1, q0), np.where(swap, q0, q1)
    d0, d1 = np.where(swap[:, None], d1, d0), np.where(swap[:, None], d0, d1)

    idx = _nearest(pix, _bc1_palette(d0, d1))
    # Equal endpoints select the 3-colour mode; index 0 stays exact there.
    idx = np.where((q0 == q1)[:, None], 0, idx).astype(np.uint32)

    bits = np.zeros(blocks.shape[0], dtype=np.uint32)
    for i in range(16):
        bits |= idx[:, i] << np.uint32(2 * i)

    out = np.empty((blocks.shape[0], 8), dtype=np.uint8)
    out[:, 0] = (q0 & 0xFF).astype(np.uint8)
    out[:, 1] = (q0 >> 8).astype(np.uint8)
    out[:, 2] = (q1 & 0xFF).astype(np.uint8)
    out[:, 3] = (q1 >> 8).astype(np.uint8)
    for i in range(4):
        out[:, 4 + i] = ((bits >> np.uint32(8 * i)) & np.uint32(0xFF)).astype(np.uint8)
    return out


def _encode_bc4_chunk(alpha: np.ndarray) -> np.ndarray:
    """(n, 16) uint8 alpha -> (n, 8) uint8 BC4 blocks (the alpha half of BC3)."""
    a = alpha.astype(np.int32)
    a0 = a.max(axis=1)
    a1 = a.min(axis=1)
    flat = a0 == a1

    # 8-value mode palette: p0 = a0, p1 = a1, p_k = ((8-k)*a0 + (k-1)*a1) / 7.
    pal = np.empty((a.shape[0], 8), dtype=np.int32)
    pal[:, 0] = a0
    pal[:, 1] = a1
    for k in range(2, 8):
        pal[:, k] = ((8 - k) * a0 + (k - 1) * a1) // 7
    idx = np.argmin(np.abs(a[:, :, None] - pal[:, None, :]), axis=2)
    idx = np.where(flat[:, None], 0, idx).astype(np.uint64)

    bits = np.zeros(a.shape[0], dtype=np.uint64)
    for i in range(16):
        bits |= idx[:, i] << np.uint64(3 * i)

    out = np.empty((a.shape[0], 8), dtype=np.uint8)
    out[:, 0] = a0.astype(np.uint8)
    out[:, 1] = a1.astype(np.uint8)
    for i in range(6):
        out[:, 2 + i] = ((bits >> np.uint64(8 * i)) & np.uint64(0xFF)).astype(np.uint8)
    return out


def encode_bc1(image: np.ndarray) -> bytes:
    """Compress an RGB(A) uint8 image (4-aligned dimensions) to BC1 / DXT1 bytes."""
    blocks = blockify(image)
    parts = [_encode_bc1_chunk(blocks[i:i + _CHUNK]) for i in range(0, len(blocks), _CHUNK)]
    if not parts:
        return b""
    return np.concatenate(parts).tobytes()


def encode_bc3(image: np.ndarray) -> bytes:
    """Compress an RGBA uint8 image (4-aligned dimensions) to BC3 / DXT5 bytes."""
    if image.shape[2] < 4:
        raise ValueError("BC3 needs an alpha channel")
    blocks = blockify(image)
    parts = []
    for i in range(0, len(blocks), _CHUNK):
        chunk = blocks[i:i + _CHUNK]
        merged = np.empty((chunk.shape[0], 16), dtype=np.uint8)
        merged[:, :8] = _encode_bc4_chunk(chunk[:, :, 3])
        merged[:, 8:] = _encode_bc1_chunk(chunk)
        parts.append(merged)
    if not parts:
        return b""
    return np.concatenate(parts).tobytes()
