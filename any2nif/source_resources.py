"""Track external resource failures that trimesh's OBJ/COLLADA loaders suppress."""

from __future__ import annotations

import io
import os
from pathlib import Path

from .errors import AnyError


def checked_resolver(source_path, trimesh, *, delegate=None):
    """Return a resolver and a post-load check for declared external resources.

    OBJ material libraries are text; other resources requested by the OBJ and
    COLLADA loaders are images. Decode those images here so a parser that catches
    image errors cannot silently turn a broken reference into a white material.
    """
    source = Path(source_path)
    libraries = set()
    if source.suffix.lower() == ".obj":
        text = source.read_text(encoding="utf-8", errors="replace").replace("\\\n", "")
        for line in text.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].lower() == "mtllib":
                libraries.add(os.path.normpath(parts[1].strip()))

    failures = {}
    checked_images = set()

    class Resolver:
        def __init__(self, delegate):
            self.delegate = delegate

        def get(self, name):
            key = (str(getattr(self.delegate, "parent", "")), str(name))
            try:
                data = self.delegate.get(name)
                if os.path.normpath(str(name).strip()) not in libraries and key not in checked_images:
                    from PIL import Image

                    with Image.open(io.BytesIO(data)) as image:
                        image.load()
                    checked_images.add(key)
                return data
            except Exception as exc:
                failures[key] = f"{name}: {exc}"
                raise

        def __getitem__(self, name):
            return self.get(name)

        def namespaced(self, namespace):
            return Resolver(self.delegate.namespaced(namespace))

        def __getattr__(self, name):
            return getattr(self.delegate, name)

    def validate():
        if failures:
            raise AnyError("source resource could not be loaded: " + "; ".join(failures.values()), 2)

    if delegate is None:
        delegate = trimesh.resolvers.FilePathResolver(str(source))
    return Resolver(delegate), validate
