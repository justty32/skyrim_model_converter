"""FBX -> glTF, via the external FBX2glTF binary."""

from __future__ import annotations

import os
import shutil
import subprocess

from .errors import AnyError


_DOWNLOAD_URL = (
    "https://github.com/facebookincubator/FBX2glTF/releases/download/"
    "v0.9.7/FBX2glTF-linux-x64"
)


def _usable_executable(path: str | None) -> str | None:
    if not path:
        return None
    candidate = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def find_fbx2gltf(explicit: str | None = None) -> str | None:
    """Find FBX2glTF by explicit path, env, repo install, then ``PATH``."""
    candidate = _usable_executable(explicit)
    if candidate:
        return candidate

    candidate = _usable_executable(os.environ.get("MODEL_CONVERTER_FBX2GLTF"))
    if candidate:
        return candidate

    repo_binary = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "bin", "FBX2glTF",
    )
    candidate = _usable_executable(repo_binary)
    if candidate:
        return candidate

    for name in ("FBX2glTF", "FBX2glTF-linux-x64"):
        candidate = shutil.which(name)
        if candidate:
            return os.path.abspath(candidate)
    return None


def to_gltf(in_path: str, workdir: str, *, fbx2gltf: str | None = None) -> str:
    """Convert an FBX into a GLB under ``workdir`` and return its absolute path.

    FBX2glTF preserves skeleton/skin data in the GLB. The downstream static
    ``gltf2nif.read_gltf`` reader rejects such inputs as skinning, and the
    any2nif CLI maps that rejection to exit code 3.
    """
    executable = find_fbx2gltf(fbx2gltf)
    if executable is None:
        raise AnyError(
            "FBX input requires the external FBX2glTF tool; set "
            "MODEL_CONVERTER_FBX2GLTF=<path> or run tools/fetch_fbx2gltf.sh. "
            f"Official download: {_DOWNLOAD_URL}",
            1,
        )

    os.makedirs(workdir, exist_ok=True)
    output_prefix = os.path.join(os.path.abspath(workdir), "fbx-converted")
    output_path = output_prefix + ".glb"
    command = [
        executable,
        "--input", os.path.abspath(in_path),
        "--output", output_prefix,
        "--binary",
        "--pbr-metallic-roughness",
        "--compute-normals", "missing",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise AnyError("FBX2glTF timed out after 300 seconds", 1) from exc
    except OSError as exc:
        raise AnyError(f"cannot run FBX2glTF: {exc}", 1) from exc

    if result.returncode != 0:
        stderr_lines = result.stderr.strip().splitlines()
        detail = "\n".join(stderr_lines[-5:]) or "no error output"
        raise AnyError(
            f"FBX2glTF failed (exit {result.returncode}): {detail}",
            2,
        )
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise AnyError("FBX2glTF produced no non-empty .glb output", 2)
    return output_path
