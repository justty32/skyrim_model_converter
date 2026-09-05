"""DDS container: the 4-byte magic + 124-byte ``DDS_HEADER`` with ``DDS_PIXELFORMAT``.

Only the FourCC path is written (``DXT1`` for BC1, ``DXT5`` for BC3), which is
what Skyrim SE's renderer and every DDS reader in the wild understand; no DX10
extension header, so the payload starts at a flat offset of 128 bytes.
"""

from __future__ import annotations

import struct

from .bcn import BC1_BLOCK_BYTES, BC3_BLOCK_BYTES

MAGIC = b"DDS "
HEADER_SIZE = 124
DDS_HEADER_BYTES = 128  # magic + header, i.e. where mip 0 starts

# DDS_HEADER.dwFlags
DDSD_CAPS = 0x00000001
DDSD_HEIGHT = 0x00000002
DDSD_WIDTH = 0x00000004
DDSD_PITCH = 0x00000008
DDSD_PIXELFORMAT = 0x00001000
DDSD_MIPMAPCOUNT = 0x00020000
DDSD_LINEARSIZE = 0x00080000

# DDS_PIXELFORMAT.dwFlags
DDPF_FOURCC = 0x00000004

# DDS_HEADER.dwCaps
DDSCAPS_COMPLEX = 0x00000008
DDSCAPS_TEXTURE = 0x00001000
DDSCAPS_MIPMAP = 0x00400000

FOURCC = {"bc1": b"DXT1", "bc3": b"DXT5"}
BLOCK_BYTES = {"bc1": BC1_BLOCK_BYTES, "bc3": BC3_BLOCK_BYTES}


def blocks_for(width: int, height: int) -> tuple[int, int]:
    """Block-grid dimensions for a mip level (a 1x1 mip still costs one block)."""
    return max(1, (width + 3) // 4), max(1, (height + 3) // 4)


def linear_size(width: int, height: int, fmt: str) -> int:
    """Compressed byte count of one mip level."""
    bw, bh = blocks_for(width, height)
    return bw * bh * BLOCK_BYTES[fmt]


def mip_sizes(width: int, height: int, mip_count: int) -> list[tuple[int, int]]:
    """The (width, height) of each mip level, halving to a floor of 1."""
    out = []
    w, h = width, height
    for _ in range(mip_count):
        out.append((w, h))
        w = max(1, w // 2)
        h = max(1, h // 2)
    return out


def full_mip_count(width: int, height: int) -> int:
    """Length of a complete chain down to 1x1: floor(log2(max(w, h))) + 1."""
    count = 1
    w, h = width, height
    while w > 1 or h > 1:
        w = max(1, w // 2)
        h = max(1, h // 2)
        count += 1
    return count


def build_header(width: int, height: int, fmt: str, mip_count: int) -> bytes:
    """The 128-byte magic + DDS_HEADER preceding the compressed mip chain."""
    if fmt not in FOURCC:
        raise ValueError(f"unsupported DDS format {fmt!r}")
    if mip_count < 1:
        raise ValueError("mip_count must be at least 1")

    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
    caps = DDSCAPS_TEXTURE
    if mip_count > 1:
        # Only a real chain earns the mipmap flag and the COMPLEX/MIPMAP caps;
        # a single-level surface is not a "complex" resource.
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_COMPLEX | DDSCAPS_MIPMAP

    header = struct.pack(
        "<7I",
        HEADER_SIZE,
        flags,
        height,
        width,
        linear_size(width, height, fmt),  # dwPitchOrLinearSize = top mip size
        0,                                # dwDepth
        mip_count,
    )
    header += b"\0" * 44  # dwReserved1[11]
    header += struct.pack("<2I", 32, DDPF_FOURCC) + FOURCC[fmt]
    header += struct.pack("<5I", 0, 0, 0, 0, 0)  # bitcount + RGBA masks
    header += struct.pack("<5I", caps, 0, 0, 0, 0)  # dwCaps1..4 + dwReserved2
    assert len(header) == HEADER_SIZE, len(header)
    return MAGIC + header


def parse_header(data: bytes) -> dict:
    """Minimal reader for the fields we write. Used by the tests and by callers
    that want to re-inspect a produced file without pulling in an image library."""
    if len(data) < DDS_HEADER_BYTES or data[:4] != MAGIC:
        raise ValueError("not a DDS file")
    size, flags, height, width, pitch, depth, mips = struct.unpack("<7I", data[4:32])
    pf_flags, fourcc = struct.unpack("<I4s", data[80:88])
    caps = struct.unpack("<I", data[108:112])[0]
    return {
        "header_size": size,
        "flags": flags,
        "width": width,
        "height": height,
        "linear_size": pitch,
        "depth": depth,
        "mip_count": mips,
        "pf_flags": pf_flags,
        "fourcc": fourcc,
        "caps": caps,
    }
