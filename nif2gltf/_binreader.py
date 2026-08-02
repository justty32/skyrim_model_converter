"""Low-level NIF binary primitives: exceptions + the byte cursor (`_Reader`).

Split out of nif_reader.py (behavior-preserving): everything here is the
self-contained "read N bytes / decode a scalar" layer that block decoders and
the top-level parser build on. The public exceptions live here because the
cursor itself raises NifError on EOF.
"""

from __future__ import annotations

import struct

import numpy as np


class NifError(Exception):
    """Unparseable / unsupported NIF -> CLI exit 2."""


class SkinnedNifError(Exception):
    """Contains skin/animation; MVP static backend rejects -> CLI exit 3."""


class _Reader:
    __slots__ = ("d", "p")

    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def seek(self, pos: int) -> None:
        self.p = pos

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise NifError("unexpected end of file")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack_from("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack_from("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack_from("<i", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack_from("<Q", self.take(8))[0]

    def f32(self) -> float:
        return struct.unpack_from("<f", self.take(4))[0]

    def vec3(self) -> tuple[float, float, float]:
        return struct.unpack_from("<3f", self.take(12))

    def mat33(self) -> np.ndarray:
        # nif.xml: stored column-major m11,m21,m31, m12,m22,m32, m13,m23,m33.
        m = struct.unpack_from("<9f", self.take(36))
        return np.array(
            [[m[0], m[3], m[6]], [m[1], m[4], m[7]], [m[2], m[5], m[8]]],
            dtype=np.float64,
        )

    def line(self) -> str:
        start = self.p
        while self.p < len(self.d) and self.d[self.p] != 0x0A:
            self.p += 1
        s = self.d[start:self.p]
        self.p += 1  # consume newline
        return s.decode("latin-1")

    def sized_string(self) -> str:
        n = self.u32()
        return self.take(n).decode("latin-1")

    def export_string(self) -> str:
        n = self.u8()
        return self.take(n).rstrip(b"\x00").decode("latin-1")
