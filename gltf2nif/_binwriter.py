"""Low-level NIF binary write primitives — the mirror of nif2gltf/_binreader.py.

`_Writer` is a growable byte buffer with scalar/vector/matrix encoders that emit
exactly the little-endian layouts nif2gltf/_binreader.py decodes. Keeping the two
symmetric is deliberate: whatever `_Reader.<x>()` reads, `_Writer.<x>()` writes,
so a round-trip through both is byte-faithful for the fields both understand.
"""

from __future__ import annotations

import struct

import numpy as np


class GltfError(Exception):
    """Unusable / unsupported glTF input -> CLI exit 2."""


class _Writer:
    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = bytearray()

    def __len__(self) -> int:
        return len(self.buf)

    def raw(self, b: bytes) -> None:
        self.buf += b

    def u8(self, v: int) -> None:
        self.buf += struct.pack("<B", v & 0xFF)

    def u16(self, v: int) -> None:
        self.buf += struct.pack("<H", v & 0xFFFF)

    def u32(self, v: int) -> None:
        self.buf += struct.pack("<I", v & 0xFFFFFFFF)

    def i32(self, v: int) -> None:
        self.buf += struct.pack("<i", v)

    def u64(self, v: int) -> None:
        self.buf += struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)

    def f32(self, v: float) -> None:
        self.buf += struct.pack("<f", v)

    def vec3(self, t) -> None:
        self.buf += struct.pack("<3f", float(t[0]), float(t[1]), float(t[2]))

    def vec4(self, t) -> None:
        self.buf += struct.pack("<4f", float(t[0]), float(t[1]), float(t[2]), float(t[3]))

    def mat33(self, m: np.ndarray) -> None:
        # nif.xml stores column-major m11,m21,m31, m12,m22,m32, m13,m23,m33
        # (see _binreader.mat33 which reads them back in that order).
        m = np.asarray(m, dtype=np.float64)
        self.buf += struct.pack(
            "<9f",
            m[0, 0], m[1, 0], m[2, 0],
            m[0, 1], m[1, 1], m[2, 1],
            m[0, 2], m[1, 2], m[2, 2],
        )

    def half3(self, t) -> None:
        self.buf += np.asarray(t[:3], dtype="<f2").tobytes()

    def half2(self, t) -> None:
        self.buf += np.asarray(t[:2], dtype="<f2").tobytes()

    def line(self, s: str) -> None:
        self.buf += s.encode("latin-1") + b"\x0a"

    def sized_string(self, s: str) -> None:
        b = s.encode("latin-1")
        self.u32(len(b))
        self.buf += b

    def export_string(self, s: str) -> None:
        # 1-byte length that INCLUDES the trailing NUL (see _binreader.export_string).
        b = s.encode("latin-1") + b"\x00"
        self.u8(len(b))
        self.buf += b
