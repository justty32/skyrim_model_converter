"""any2nif error types. `code` is the CLI exit code the error should produce."""

from __future__ import annotations


class AnyError(Exception):
    """Any failure in the normalise -> glTF -> NIF pipeline.

    code 1 = general (args, tool missing, write failure)
    code 2 = source parse failure (unreadable / unsupported model file)
    code 3 = skinned / animated source rejected by the static backend
    """

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code
