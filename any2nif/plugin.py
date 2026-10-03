"""Optional ESL plugin for a converted package: one STAT record, optionally a test cell.

The converter does not write plugin bytes itself. It emits a reviewable ModForge spec and
runs the ModForge CLI as a black box (same pattern as PROTOCOL.md's MODFORGE_* hooks):

* ``MODFORGE_CLI``  full command prefix, e.g. ``dotnet /x/ModForge.Cli.dll`` (shell-split)
* ``MODFORGE_DIR``  ModForge checkout; default is the sibling ``../ModForge`` of this repo

OBND is derived from the written NIF's triangle-referenced render vertices, because a STAT
with zero bounds is culled in game once it is large.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess

from nif2gltf.nif_reader import read_nif

from .errors import AnyError

INT16_MIN, INT16_MAX = -32768, 32767
# Skyrim.esm forms used by the optional test cell (looked up with `ModForge find`).
BREEZEHOME_CELL = "Skyrim.esm:0x0165A8"      # lighting template for the new interior
FLOOR_STATIC = "Skyrim.esm:0x1044AA"         # WRIntFloorSTMid01Large, OBND -512..512, top z=0
FLOOR_TILE = 1024
COC_MARKER = "Skyrim.esm:0x000032"           # COCMarkerHeading: where `coc` drops the player
MAX_FLOOR_TILES = 15                         # per axis; ~15k units, beyond that use a real cell
MODFORGE_TIMEOUT = 600


def object_bounds(nif_bytes):
    """Return Skyrim-space int OBND ((min x,y,z), (max x,y,z)) enclosing the render mesh."""
    points = []
    for mesh in read_nif(nif_bytes):
        for index in {i for tri in mesh.triangles for i in tri}:
            gx, gy, gz = mesh.positions[index]
            points.append((gx, -gz, gy))  # reader returns glTF axes: Skyrim (x,y,z) -> (x,z,-y)
    if not points or not all(math.isfinite(v) for p in points for v in p):
        raise AnyError("plugin: NIF has no finite triangle vertices for object bounds")
    low = tuple(math.floor(min(p[a] for p in points)) for a in range(3))
    high = tuple(math.ceil(max(p[a] for p in points)) for a in range(3))
    if min(low) < INT16_MIN or max(high) > INT16_MAX:
        raise AnyError(f"plugin: model bounds {low}..{high} exceed Skyrim's int16 OBND; "
                       "reduce --scale/--unit or omit --plugin")
    return low, high


def editor_id(name):
    """ASCII EditorID from the (already sanitised) package name."""
    parts = [p for p in name.replace("-", "_").split("_") if p]
    return "A2N_" + "".join(p[:1].upper() + p[1:] for p in parts)


def _vec(values):
    return {"x": values[0], "y": values[1], "z": values[2]}


def _tile_offsets(low, high):
    """Floor tile centres covering the model footprint plus one tile of margin."""
    offsets = []
    for axis in (0, 1):
        reach = max(abs(low[axis]), abs(high[axis])) + FLOOR_TILE
        count = 2 * math.ceil((reach - FLOOR_TILE / 2) / FLOOR_TILE) + 1
        if count > MAX_FLOOR_TILES:
            raise AnyError(f"plugin: model is too large for the generated test cell floor "
                           f"({count} tiles per axis); omit --test-cell")
        offsets.append([(i - count // 2) * FLOOR_TILE for i in range(count)])
    return offsets


def build_spec(name, mesh_relative, low, high, *, test_cell):
    """ModForge spec dict. ``mesh_relative`` is the Data-relative ``meshes/...`` path."""
    parts = Path(mesh_relative).parts
    if not parts or parts[0].lower() != "meshes":
        raise AnyError(f"plugin: mesh path must start with meshes/: {mesh_relative}")
    eid = editor_id(name)
    spec = {
        "pluginName": f"any2nif_{name}.esp",
        "esl": True,
        "statics": [{"editorId": eid, "model": "\\".join(parts[1:]),
                     "objectBoundsMin": _vec(low), "objectBoundsMax": _vec(high)}],
    }
    if test_cell:
        cell = f"{eid}_TestCell"
        xs, ys = _tile_offsets(low, high)
        placements = [{"base": FLOOR_STATIC, "cell": cell, "position": _vec((x, y, 0))}
                      for x in xs for y in ys]
        # Rest the model's lowest vertex on the floor top (z=0), never sink it.
        placements.append({"base": eid, "cell": cell, "position": _vec((0, 0, max(0, -low[2])))})
        # Stand south of the model facing +Y (heading 0) so it is in view after `coc`.
        placements.append({"base": COC_MARKER, "cell": cell,
                           "position": _vec((0, min(low[1], 0) - 256, 8))})
        spec["cells"] = [{"editorId": cell, "name": f"any2nif test {name}",
                          "template": BREEZEHOME_CELL}]
        spec["placements"] = placements
    return spec


def modforge_command():
    """Command prefix that runs the ModForge CLI, or raise with how to configure it."""
    explicit = os.environ.get("MODFORGE_CLI")
    if explicit:
        return shlex.split(explicit)
    root = Path(os.environ.get("MODFORGE_DIR") or Path(__file__).resolve().parents[2] / "ModForge")
    project = root / "src" / "ModForge.Cli"
    dll = project / "bin" / "Debug" / "net10.0" / "ModForge.Cli.dll"
    dotnet = shutil.which("dotnet")
    if dotnet and dll.is_file():
        return [dotnet, str(dll)]
    raise AnyError(f"plugin: ModForge CLI not found (looked for {dll} and dotnet); build it with "
                   f"`dotnet build {project}` or set MODFORGE_CLI / MODFORGE_DIR")


def write_plugin(data_dir, name, nif_path, *, test_cell):
    """Write modforge-spec.json and the ESL-flagged .esp into data_dir; return manifest info."""
    low, high = object_bounds(nif_path.read_bytes())
    spec = build_spec(name, nif_path.relative_to(data_dir).as_posix(), low, high,
                      test_cell=test_cell)
    spec_path = data_dir / "modforge-spec.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    esp = data_dir / spec["pluginName"]
    command = modforge_command() + ["build", str(spec_path), str(esp)]
    before = set(data_dir.iterdir())
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=MODFORGE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnyError(f"plugin: cannot run ModForge: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        raise AnyError("plugin: ModForge build failed: " + " | ".join(tail))
    try:
        header = esp.read_bytes()[:4]
    except OSError:
        header = b""
    if header != b"TES4":
        raise AnyError(f"plugin: ModForge did not write a plugin at {esp.name}")
    # ModForge may drop sidecars (<plugin>.requires.txt names non-vanilla masters). Only
    # vanilla forms are referenced here, so any sidecar means the build is not what we asked.
    extras = sorted(p.name for p in set(data_dir.iterdir()) - before - {esp})
    if extras:
        raise AnyError(f"plugin: unexpected ModForge output {extras}; plugin must need only vanilla masters")
    info = {"file": esp.name, "spec": spec_path.name, "editor_id": spec["statics"][0]["editorId"],
            "object_bounds": [list(low), list(high)]}
    if test_cell:
        info["test_cell"] = spec["cells"][0]["editorId"]
    return info
