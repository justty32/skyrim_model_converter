"""BC1/BC3 encoder + DDS container, cross-checked against Pillow's own DDS reader.

Every pixel assertion decodes our output with `PIL.Image.open`, i.e. a third-party
BC decoder. If the encoder and the assertions were both wrong in the same way,
Pillow would still disagree -- that is the point of routing through it.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.tex2dds_fixtures import (
    decode_dds,
    flat_rgb,
    gradient_rgb,
    gradient_rgba,
    opaque_rgba,
    rmse,
)
from tex2dds import encode_dds, full_mip_count, linear_size, mip_sizes, parse_header

# Colours that survive RGB565 exactly: r/b are (v<<3)|(v>>2), g is (v<<2)|(v>>4).
EXACT_565 = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255), (132, 56, 255)]
# Worst-case RGB565 rounding error is half a step: 4 for the 5-bit channels.
QUANT_TOLERANCE = 4


# ------------------------------------------------------------------ flat colour fidelity

@pytest.mark.parametrize("colour", EXACT_565)
def test_flat_565_colour_is_bit_exact(colour):
    source = flat_rgb(16, colour)
    back = decode_dds(encode_dds(source, fmt="bc1"))
    assert np.abs(back[:, :, :3] - np.array(colour, dtype=np.int16)).max() == 0


@pytest.mark.parametrize("colour", [(123, 77, 201), (7, 250, 19), (40, 41, 42)])
def test_flat_arbitrary_colour_within_quantisation_bound(colour):
    source = flat_rgb(16, colour)
    back = decode_dds(encode_dds(source, fmt="bc1"))
    assert np.abs(back[:, :, :3] - np.array(colour, dtype=np.int16)).max() <= QUANT_TOLERANCE


def test_bc1_readback_is_fully_opaque():
    """4-colour mode only: a 3-colour block would decode index 3 as transparent."""
    noise = np.random.default_rng(7).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    for source in (noise, flat_rgb(16, (10, 20, 30)), gradient_rgb(32, 32)):
        back = decode_dds(encode_dds(source, fmt="bc1"))
        assert back[:, :, 3].min() == 255


# ---------------------------------------------------------------------- gradient quality

def test_gradient_rmse_within_bc1_budget():
    source = gradient_rgb(64, 64)
    back = decode_dds(encode_dds(source, fmt="bc1"))
    assert rmse(back[:, :, :3], source) < 6.0


def test_bc3_preserves_colour_and_alpha_ramp():
    source = gradient_rgba(64, 64)
    data = encode_dds(source, fmt="bc3")
    assert parse_header(data)["fourcc"] == b"DXT5"
    back = decode_dds(data)
    assert rmse(back[:, :, :3], source[:, :, :3]) < 6.0
    # BC4 alpha: 8 interpolated levels across each block's own range.
    assert np.abs(back[:, :, 3] - source[:, :, 3].astype(np.int16)).max() <= 8


def test_endpoint_fit_beats_a_flat_block_mean():
    """Sanity that the principal-axis fit is actually fitting: a two-tone block
    must come back as two tones, not as their average."""
    source = np.zeros((4, 4, 3), dtype=np.uint8)
    source[:, :2] = (255, 0, 0)
    source[:, 2:] = (0, 0, 255)
    back = decode_dds(encode_dds(source, fmt="bc1", mipmaps=False, resize="none"))
    assert np.array_equal(back[0, 0, :3], np.array([255, 0, 0], dtype=np.int16))
    assert np.array_equal(back[0, 3, :3], np.array([0, 0, 255], dtype=np.int16))


def test_block_raster_order_is_preserved():
    """Distinct quadrants must land where they started (catches a blockify transpose)."""
    source = np.zeros((8, 8, 3), dtype=np.uint8)
    source[:4, :4] = (255, 0, 0)
    source[:4, 4:] = (0, 255, 0)
    source[4:, :4] = (0, 0, 255)
    source[4:, 4:] = (255, 255, 255)
    back = decode_dds(encode_dds(source, fmt="bc1", mipmaps=False, resize="none"))
    assert np.array_equal(back[:, :, :3], source.astype(np.int16))


# ------------------------------------------------------------------- format selection

def test_auto_picks_bc3_only_when_alpha_is_real():
    assert parse_header(encode_dds(gradient_rgba(32, 32)))["fourcc"] == b"DXT5"
    assert parse_header(encode_dds(opaque_rgba(32, 32)))["fourcc"] == b"DXT1"
    assert parse_header(encode_dds(gradient_rgb(32, 32)))["fourcc"] == b"DXT1"


def test_explicit_format_overrides_auto():
    assert parse_header(encode_dds(gradient_rgba(32, 32), fmt="bc1"))["fourcc"] == b"DXT1"
    assert parse_header(encode_dds(gradient_rgb(32, 32), fmt="bc3"))["fourcc"] == b"DXT5"


# --------------------------------------------------------------------------- mip chain

@pytest.mark.parametrize("width,height", [(64, 64), (8, 2), (32, 128), (1, 1), (4, 16)])
def test_mip_count_matches_the_formula(width, height):
    source = gradient_rgb(width, height)
    header = parse_header(encode_dds(source, fmt="bc1", resize="none"))
    assert (header["width"], header["height"]) == (width, height)
    assert header["mip_count"] == full_mip_count(width, height)
    expected = int(np.floor(np.log2(max(width, height)))) + 1
    assert header["mip_count"] == expected


@pytest.mark.parametrize("width,height", [(64, 64), (8, 2), (32, 16)])
def test_file_length_is_header_plus_every_mip(width, height):
    for fmt in ("bc1", "bc3"):
        data = encode_dds(gradient_rgba(width, height), fmt=fmt, resize="none")
        count = full_mip_count(width, height)
        expected = 128 + sum(linear_size(w, h, fmt) for w, h in mip_sizes(width, height, count))
        assert len(data) == expected
        assert parse_header(data)["linear_size"] == linear_size(width, height, fmt)


def test_no_mipmaps_writes_one_level_only():
    data = encode_dds(gradient_rgb(64, 64), fmt="bc1", mipmaps=False)
    header = parse_header(data)
    assert header["mip_count"] == 1
    assert len(data) == 128 + linear_size(64, 64, "bc1")
    # A single-level surface is not a mip chain and not a COMPLEX resource.
    assert not header["flags"] & 0x00020000  # DDSD_MIPMAPCOUNT
    assert not header["caps"] & 0x00400000  # DDSCAPS_MIPMAP
    assert header["caps"] & 0x00001000  # DDSCAPS_TEXTURE


def test_header_flags_and_caps_for_a_mipmapped_texture():
    header = parse_header(encode_dds(gradient_rgb(64, 64), fmt="bc1"))
    assert header["header_size"] == 124
    expected_flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | 0x20000
    assert header["flags"] & expected_flags == expected_flags
    assert header["caps"] & (0x8 | 0x400000 | 0x1000) == (0x8 | 0x400000 | 0x1000)
    assert header["pf_flags"] == 0x4  # DDPF_FOURCC
    assert header["depth"] == 0


def test_sub_block_sizes_pad_without_black_bleed():
    """A 2x2 image occupies a whole 4x4 block; edge padding must not darken it."""
    source = flat_rgb(2, (255, 0, 0))
    data = encode_dds(source, fmt="bc1", resize="none", mipmaps=False)
    assert len(data) == 128 + linear_size(2, 2, "bc1")
    back = decode_dds(data)
    assert back.shape[:2] == (2, 2)
    assert np.abs(back[:, :, :3] - np.array([255, 0, 0], dtype=np.int16)).max() == 0


def test_smallest_mips_of_a_flat_texture_keep_the_colour():
    """Reads mip 0 only (Pillow stops there), but the chain must still be written."""
    data = encode_dds(flat_rgb(64, (0, 0, 255)), fmt="bc1")
    assert parse_header(data)["mip_count"] == 7
    assert np.abs(decode_dds(data)[:, :, :3] - np.array([0, 0, 255], dtype=np.int16)).max() == 0


# ----------------------------------------------------------------------- normal maps

def test_normal_map_flips_green_and_forces_bc3():
    source = flat_rgb(16, (128, 200, 255))
    data = encode_dds(source, normal_map=True)
    assert parse_header(data)["fourcc"] == b"DXT5"
    back = decode_dds(data)
    # DirectX convention: g -> 255 - g, then RGB565 rounding.
    assert abs(int(back[0, 0, 1]) - (255 - 200)) <= QUANT_TOLERANCE
    assert abs(int(back[0, 0, 0]) - 128) <= QUANT_TOLERANCE
    assert back[0, 0, 3] == 255  # glossiness defaults to fully glossy


def test_normal_map_keeps_an_existing_alpha_as_glossiness():
    source = np.zeros((16, 16, 4), dtype=np.uint8)
    source[:, :, :3] = (128, 128, 255)
    source[:, :, 3] = 64
    back = decode_dds(encode_dds(source, normal_map=True))
    assert abs(int(back[0, 0, 3]) - 64) <= 2


# --------------------------------------------------------------------------- resizing

def test_resize_pow2_snaps_dimensions_and_none_keeps_them():
    source = gradient_rgb(60, 100)
    snapped = parse_header(encode_dds(source, resize="pow2"))
    assert (snapped["width"], snapped["height"]) == (64, 128)
    kept = parse_header(encode_dds(source, resize="none"))
    assert (kept["width"], kept["height"]) == (60, 100)


def test_already_pow2_is_untouched_by_resize():
    header = parse_header(encode_dds(gradient_rgb(32, 64), resize="pow2"))
    assert (header["width"], header["height"]) == (32, 64)


def test_rejects_unknown_format_and_resize():
    from tex2dds import Tex2ddsError

    with pytest.raises(Tex2ddsError):
        encode_dds(gradient_rgb(8, 8), fmt="bc7")
    with pytest.raises(Tex2ddsError):
        encode_dds(gradient_rgb(8, 8), resize="half")
