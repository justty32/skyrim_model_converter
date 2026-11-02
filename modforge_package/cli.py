"""GLB -> existing any2nif package -> one StaticSpec -> existing ModForge package."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from any2nif.package import _asset_name
from any2nif.errors import AnyError
from nif2gltf._binreader import NifError, SkinnedNifError
from .safety import MANIFEST, SCHEMA, check_destination, hashes, publish, reject_links, staging_directory
from .validation import converter_assets, embedded_glb, static_readback

ROOT = Path(__file__).resolve().parents[1]


def run(argv):
    result = subprocess.run(list(map(str, argv)), cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=300)
    if result.stdout:
        print(result.stdout.rstrip(), file=sys.stderr)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    if result.returncode:
        raise ValueError(f"tool failed (exit {result.returncode}): {argv[0]}")
    return result.stdout


def dotnet_path(path, dotnet):
    if os.name != "nt" and str(dotnet).lower().endswith(".exe"):
        return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
    return str(path)


def package(args):
    source = Path(args.source).absolute()
    destination = Path(args.output).absolute()
    dll = Path(args.modforge_dll).absolute()
    for path in (source, destination, dll):
        reject_links(path)
    source, destination, dll = (p.resolve() for p in (source, destination, dll))
    for path in (source, dll):
        if not path.is_file():
            raise ValueError(f"missing input: {path}")
    check_destination(destination, source)
    if destination == dll or destination in dll.parents:
        raise ValueError("output cannot contain the ModForge DLL")
    name = args.asset_name
    if _asset_name(name) != name:
        raise ValueError("asset name must be lowercase ASCII letters/digits/underscore/hyphen")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", args.editor_id):
        raise ValueError("editor ID must be 1-80 ASCII identifier characters")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}\.esp", args.plugin):
        raise ValueError("plugin must be a simple ASCII .esp filename")
    embedded_glb(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with staging_directory(destination.parent) as state:
        work = state["path"]
        converted, staged = work / "converted", work / "package"
        run([sys.executable, "-m", "any2nif", source, converted, "--package",
             "--collision", "box", "--asset-name", name])
        converted_manifest, collision = converter_assets(converted, name)
        model = converted_manifest["mesh"].split("/", 1)[1]
        spec = {"pluginName": args.plugin, "esl": False,
                "statics": [{"editorId": args.editor_id, "model": model}]}
        spec_path = work / "static-spec.json"
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

        def modforge(*command):
            return run([args.dotnet, dotnet_path(dll, args.dotnet), *command])

        modforge("package", dotnet_path(spec_path, args.dotnet), dotnet_path(staged, args.dotnet),
                 "--assets", dotnet_path(converted, args.dotnet))
        # The existing bundler canonicalizes only the first folder's spelling.
        expected = {("Meshes" if p.startswith("meshes/") else "Textures") + "/" + p.split("/", 1)[1]: h
                    for p, h in converted_manifest["sha256"].items()}
        copied = hashes(staged)
        if set(copied) != {*expected, args.plugin} or any(copied[p] != h for p, h in expected.items()):
            raise ValueError("ModForge package omitted/changed assets or contains unexpected files")
        catalog, exported = work / "readback.db", work / "readback.json"
        modforge("catalog", "build", dotnet_path(catalog, args.dotnet),
                 dotnet_path(staged / args.plugin, args.dotnet))
        modforge("catalog", "export-json", dotnet_path(catalog, args.dotnet),
                 dotnet_path(exported, args.dotnet))
        readback = static_readback(json.loads(exported.read_text(encoding="utf-8")),
                                   args.editor_id, model, args.plugin)
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
            raise ValueError("source changed during packaging")
        shutil.copyfile(converted / "converter-package.json", staged / "converter-provenance.json")
        manifest = {"schema": SCHEMA, "sourceSha256": source_hash, "spec": spec,
                    "permittedDataFolders": ["Meshes", "Textures"],
                    "static": readback, "collision": collision,
                    "sha256": hashes(staged)}
        (staged / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        check_destination(destination, source)
        publish(staged, destination)
        state["published"] = True
    print(f"packaged {args.plugin} -> {destination}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="self-contained static GLB")
    parser.add_argument("output", help="new or unchanged owned output directory")
    parser.add_argument("--modforge-dll", required=True, help="built ModForge.Cli.dll")
    parser.add_argument("--dotnet", default="dotnet", help="dotnet executable; Windows .exe works under WSL")
    parser.add_argument("--editor-id", default="SyntheticBox")
    parser.add_argument("--plugin", default="SyntheticBox.esp")
    parser.add_argument("--asset-name", default="synthetic_box")
    args = parser.parse_args(argv)
    try:
        return package(args)
    except (ValueError, OSError, KeyError, TypeError, IndexError, struct.error, AnyError, NifError, SkinnedNifError,
            subprocess.SubprocessError) as exc:
        print(f"error: model package: {exc}", file=sys.stderr)
        return 2
