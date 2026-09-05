"""tex2dds CLI contract: exit codes (0 ok / 1 general / 2 parse) and flag behaviour."""

from __future__ import annotations

import os

import numpy as np

from tests.tex2dds_fixtures import (
    decode_dds,
    flat_rgb,
    gradient_rgb,
    gradient_rgba,
    write_png,
)
from tex2dds import full_mip_count, linear_size, parse_header
from tex2dds.cli import main


def _png(tmp_path, name, array):
    return write_png(str(tmp_path / name), array)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


# ------------------------------------------------------------------------- exit codes

def test_convert_returns_0_and_writes_the_file(tmp_path):
    src = _png(tmp_path, "diffuse.png", gradient_rgb(32, 32))
    out = str(tmp_path / "diffuse.dds")
    assert main([src, out]) == 0
    assert os.path.exists(out)
    assert parse_header(_read(out))["fourcc"] == b"DXT1"


def test_missing_arguments_return_1(tmp_path):
    assert main([]) == 1
    assert main([str(tmp_path / "only.png")]) == 1


def test_missing_source_returns_1(tmp_path):
    assert main([str(tmp_path / "nope.png"), str(tmp_path / "out.dds")]) == 1


def test_invalid_flag_value_returns_1(tmp_path):
    src = _png(tmp_path, "a.png", gradient_rgb(8, 8))
    assert main([src, str(tmp_path / "a.dds"), "--format", "bc7"]) == 1


def test_undecodable_source_returns_2(tmp_path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"this is definitely not a PNG")
    assert main([str(bad), str(tmp_path / "out.dds")]) == 2


def test_unwritable_target_returns_1(tmp_path):
    src = _png(tmp_path, "a.png", gradient_rgb(8, 8))
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory")
    assert main([src, str(blocker / "out.dds")]) == 1


def test_help_returns_0(capsys):
    assert main(["--help"]) == 0


# ------------------------------------------------------------------------------ flags

def test_format_flag_overrides_auto_detection(tmp_path):
    src = _png(tmp_path, "alpha.png", gradient_rgba(32, 32))
    auto_out = str(tmp_path / "auto.dds")
    forced_out = str(tmp_path / "forced.dds")
    assert main([src, auto_out]) == 0
    assert main([src, forced_out, "--format", "bc1"]) == 0
    assert parse_header(_read(auto_out))["fourcc"] == b"DXT5"
    assert parse_header(_read(forced_out))["fourcc"] == b"DXT1"


def test_no_mipmaps_flag(tmp_path):
    src = _png(tmp_path, "d.png", gradient_rgb(64, 64))
    chained = str(tmp_path / "chained.dds")
    single = str(tmp_path / "single.dds")
    assert main([src, chained]) == 0
    assert main([src, single, "--no-mipmaps"]) == 0
    assert parse_header(_read(chained))["mip_count"] == full_mip_count(64, 64)
    assert parse_header(_read(single))["mip_count"] == 1
    assert len(_read(single)) == 128 + linear_size(64, 64, "bc1")


def test_normal_map_flag_forces_bc3_and_flips_green(tmp_path):
    src = _png(tmp_path, "n.png", flat_rgb(16, (128, 200, 255)))
    plain = str(tmp_path / "plain.dds")
    normal = str(tmp_path / "normal.dds")
    assert main([src, plain]) == 0
    assert main([src, normal, "--normal-map"]) == 0
    assert parse_header(_read(plain))["fourcc"] == b"DXT1"
    assert parse_header(_read(normal))["fourcc"] == b"DXT5"
    green_plain = int(decode_dds(_read(plain))[0, 0, 1])
    green_normal = int(decode_dds(_read(normal))[0, 0, 1])
    assert abs(green_plain - 200) <= 4
    assert abs(green_normal - (255 - 200)) <= 4


def test_resize_flag(tmp_path):
    src = _png(tmp_path, "odd.png", gradient_rgb(60, 100))
    snapped = str(tmp_path / "snapped.dds")
    kept = str(tmp_path / "kept.dds")
    assert main([src, snapped, "--resize", "pow2"]) == 0
    assert main([src, kept, "--resize", "none"]) == 0
    snapped_header = parse_header(_read(snapped))
    assert (snapped_header["width"], snapped_header["height"]) == (64, 128)
    kept_header = parse_header(_read(kept))
    assert (kept_header["width"], kept_header["height"]) == (60, 100)


def test_pow2_is_the_default_resize_mode(tmp_path):
    src = _png(tmp_path, "odd2.png", gradient_rgb(60, 100))
    out = str(tmp_path / "default.dds")
    assert main([src, out]) == 0
    header = parse_header(_read(out))
    assert (header["width"], header["height"]) == (64, 128)


def test_creates_missing_output_directories(tmp_path):
    src = _png(tmp_path, "a.png", gradient_rgb(8, 8))
    out = str(tmp_path / "deep" / "nested" / "a.dds")
    assert main([src, out]) == 0
    assert os.path.exists(out)


def test_round_trip_through_the_cli_matches_the_source(tmp_path):
    """The one end-to-end check: PNG in, DDS out, decoded by Pillow, compared."""
    source = gradient_rgb(64, 64)
    src = _png(tmp_path, "grad.png", source)
    out = str(tmp_path / "grad.dds")
    assert main([src, out]) == 0
    back = decode_dds(_read(out))
    assert back.shape == (64, 64, 4)
    assert np.sqrt(((back[:, :, :3] - source) ** 2).mean()) < 6.0


def test_dds_input_is_accepted(tmp_path):
    """DDS is in the input list, so our own output must be re-readable as a source."""
    src = _png(tmp_path, "a.png", gradient_rgb(32, 32))
    first = str(tmp_path / "first.dds")
    second = str(tmp_path / "second.dds")
    assert main([src, first]) == 0
    assert main([first, second, "--format", "bc1"]) == 0
    assert parse_header(_read(second))["width"] == 32
