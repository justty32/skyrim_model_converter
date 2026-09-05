"""tex2dds error type. `code` is the CLI exit code the error should produce."""

from __future__ import annotations


class Tex2ddsError(Exception):
    """Any failure in the image -> DDS pipeline.

    code 1 = general (args, unreadable path, write failure)
    code 2 = source image parse failure (not an image / unsupported encoding)
    """

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code
