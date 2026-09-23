#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import add_component as component
import kicad_lib_merge as merge


MANIFEST = ".avionics-library.json"
SYMBOL_LIBRARY = "Avionics_Symbols.kicad_sym"
FOOTPRINT_DIRECTORY = "Avionics_Feet.pretty"
MODEL_DIRECTORY = "Models"
MANAGED_ROOTS = (SYMBOL_LIBRARY, FOOTPRINT_DIRECTORY, MODEL_DIRECTORY)
MODEL_RE = re.compile(r'\(model\s+"((?:\\.|[^"])*)"')


class VendorError(ValueError):
    pass


def run(command, cwd=None, text=False):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=text)
    if result.returncode != 0:
        stderr = result.stderr.strip() if text else result.stderr.decode(
            "utf-8", "replace").strip()
        raise VendorError(stderr or f"command failed: {' '.join(command)}")
    return result.stdout


def git(repo, arguments, text=False):
    return run(["git", "-C", str(repo)] + list(arguments), text=text)


def resolve_ref(repo, ref):
    return git(repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"],
               text=True).strip()


def managed_path(path):
    return (path == SYMBOL_LIBRARY or
            (path.startswith(FOOTPRINT_DIRECTORY + "/") and
             path.endswith(".kicad_mod")) or
            (path.startswith(MODEL_DIRECTORY + "/") and
             path.lower().endswith((".step", ".stp"))))


def snapshot_from_ref(repo, ref):
    commit = resolve_ref(repo, ref)
    output = git(repo, ["ls-tree", "-r", "--name-only", "-z", commit,
                        "--"] + list(MANAGED_ROOTS))
    paths = [path.decode("utf-8") for path in output.split(b"\0") if path]
    files = {}
    for path in paths:
        if not managed_path(path):
            continue
        files[path] = git(repo, ["show", f"{commit}:{path}"])
    if SYMBOL_LIBRARY not in files:
        raise VendorError(f"{commit} does not contain {SYMBOL_LIBRARY}")
    return commit, files


def snapshot_from_directory(directory):
    root = Path(directory)
    files = {}
    symbol = root / SYMBOL_LIBRARY
    if symbol.is_symlink():
        raise VendorError(f"refusing symlink in snapshot: {symbol}")
    if symbol.is_file():
        files[SYMBOL_LIBRARY] = symbol.read_bytes()
    for dirname in (FOOTPRINT_DIRECTORY, MODEL_DIRECTORY):
        base = root / dirname
        if base.is_symlink():
            raise VendorError(f"refusing symlink in snapshot: {base}")
        if not base.exists():
            continue
        if not base.is_dir():
            raise VendorError(f"managed path is not a directory: {base}")
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                raise VendorError(f"refusing symlink in snapshot: {path}")
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if managed_path(relative):
                    files[relative] = path.read_bytes()
    return files


def projectize_model_paths(files, destination):
    prefix = Path(destination).as_posix().strip('/')
    canonical = b"${KIPRJMOD}/Models/"
    project = f"${{KIPRJMOD}}/{prefix}/Models/".encode("utf-8")
    output = dict(files)
    for path, data in files.items():
        if path.startswith(FOOTPRINT_DIRECTORY + "/"):
            output[path] = data.replace(canonical, project)
    return output


def canonicalize_model_paths(files, destination):
    prefix = Path(destination).as_posix().strip('/')
    project = f"${{KIPRJMOD}}/{prefix}/Models/".encode("utf-8")
    canonical = b"${KIPRJMOD}/Models/"
    output = dict(files)
    for path, data in files.items():
        if path.startswith(FOOTPRINT_DIRECTORY + "/"):
            output[path] = data.replace(project, canonical)
    return output


def read_manifest(destination):
    path = Path(destination) / MANIFEST
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VendorError(f"invalid {path}: {exc}") from exc
    if data.get("schema") != 1 or not data.get("commit"):
        raise VendorError(f"unsupported or incomplete manifest: {path}")
    return data


def choose_file(path, base, ours, theirs):
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    raise VendorError(f"conflict in {path}: local and upstream both changed")


def merge_snapshots(base, ours, theirs):
    conflicts = []
    output = {}
    symbol_values = (base.get(SYMBOL_LIBRARY), ours.get(SYMBOL_LIBRARY),
                     theirs.get(SYMBOL_LIBRARY))
    if any(value is None for value in symbol_values):
        conflicts.append(f"{SYMBOL_LIBRARY}: library cannot be deleted")
    else:
        try:
            output[SYMBOL_LIBRARY] = merge.merge_texts(
                *(value.decode("utf-8") for value in symbol_values)).encode(
                    "utf-8")
        except (UnicodeDecodeError, merge.MergeError) as exc:
            conflicts.append(f"{SYMBOL_LIBRARY}: {exc}")

    paths = ((set(base) | set(ours) | set(theirs)) - {SYMBOL_LIBRARY})
    for path in sorted(paths):
        try:
            value = choose_file(path, base.get(path), ours.get(path),
                                theirs.get(path))
        except VendorError as exc:
            conflicts.append(str(exc))
            continue
        if value is not None:
            output[path] = value
    if conflicts:
        raise VendorError("snapshot conflicts:\n  " + "\n  ".join(conflicts))
    return output


def validate_snapshot(files):
    try:
        symbols, order = merge.split_tops(
            files[SYMBOL_LIBRARY].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, merge.MergeError) as exc:
        raise VendorError(f"invalid symbol library: {exc}") from exc
    footprint_names = set()
    for path, data in files.items():
        if not path.startswith(FOOTPRINT_DIRECTORY + "/"):
            continue
        if not path.endswith(".kicad_mod"):
            raise VendorError(f"unexpected file in footprint library: {path}")
        try:
            text = data.decode("utf-8")
            start = merge._skip_space(text, 0, len(text))
            end = merge._expr_end(text, start)
            kind, pos = merge._atom(text, start + 1, end - 1)
            if kind not in ("footprint", "module"):
                raise VendorError(f"{path}: root is {kind!r}")
            pos = merge._skip_space(text, pos, end - 1)
            if pos >= end - 1:
                raise VendorError(f"{path}: footprint name is missing")
            if text[pos] == '"':
                name, _ = merge._string(text, pos, end - 1)
            else:
                name, _ = merge._atom(text, pos, end - 1)
            expected = Path(path).stem
            if name != expected:
                raise VendorError(
                    f"{path}: footprint name is {name!r}, expected {expected!r}")
            footprint_names.add(expected)
            for model_reference in MODEL_RE.findall(text):
                if not model_reference.startswith("${KIPRJMOD}/"):
                    continue
                marker = "/Models/"
                if marker not in model_reference:
                    raise VendorError(
                        f"{path}: unsupported project model path "
                        f"{model_reference!r}")
                model_name = model_reference.split(marker, 1)[1]
                model_path = f"{MODEL_DIRECTORY}/{model_name}"
                if model_path not in files:
                    raise VendorError(
                        f"{path}: missing model {model_name!r}")
            if text[end:].strip():
                raise VendorError(f"{path}: trailing content")
        except (UnicodeDecodeError, IndexError, merge.MergeError) as exc:
            raise VendorError(f"invalid footprint {path}: {exc}") from exc
    for name in order:
        try:
            reference = component.props(symbols[name]).get("Footprint", "")
        except (component.ValidationError, merge.MergeError) as exc:
            raise VendorError(f"invalid symbol {name}: {exc}") from exc
        if reference.startswith("Avionics_Feet:"):
            footprint = reference.split(':', 1)[1]
            if footprint not in footprint_names:
                raise VendorError(
                    f"symbol {name}: missing footprint {footprint!r}")


def copy_unmanaged(source, destination):
    source = Path(source)
    if not source.exists():
        return
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise VendorError(f"refusing symlink in snapshot: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        if relative == MANIFEST or managed_path(relative):
            continue
        target = Path(destination) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def write_snapshot(destination, files, manifest):
    target = Path(destination)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".avionics-vendor-", dir=parent))
    backup = None
    try:
        copy_unmanaged(target, temporary)
        for relative, data in files.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (temporary / MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if target.exists() or target.is_symlink():
            if target.is_symlink():
                raise VendorError(f"refusing to replace symlink: {target}")
            backup = Path(tempfile.mkdtemp(
                prefix=".avionics-old-", dir=parent))
            backup.rmdir()
            os.replace(target, backup)
        try:
            os.replace(temporary, target)
            temporary = None
        except BaseException:
            if backup is not None:
                os.replace(backup, target)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def safe_destination(project, destination):
    relative = Path(destination)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise VendorError("destination must be a relative directory in project")
    project_path = Path(project).resolve()
    target = (project_path / relative).resolve()
    if target == project_path or project_path not in target.parents:
        raise VendorError("destination must be a dedicated project subdirectory")
    return target


def update(project, destination, source_repo, source_ref="HEAD",
           source_name=None, base_ref=None):
    target = safe_destination(project, destination)
    new_commit, upstream = snapshot_from_ref(source_repo, source_ref)
    upstream = projectize_model_paths(upstream, destination)
    current_manifest = read_manifest(target)
    current = snapshot_from_directory(target) if target.exists() else {}

    if current_manifest is None:
        if current and not base_ref:
            raise VendorError(
                "existing snapshot has no manifest; provide --base-ref once")
        if current:
            old_commit, base = snapshot_from_ref(source_repo, base_ref)
            base = projectize_model_paths(base, destination)
            merged = merge_snapshots(base, current, upstream)
        else:
            old_commit, base, merged = None, {}, upstream
    else:
        old_commit = current_manifest["commit"]
        _, base = snapshot_from_ref(source_repo, old_commit)
        base = projectize_model_paths(base, destination)
        merged = merge_snapshots(base, current, upstream)

    validate_snapshot(merged)
    modified_paths = sorted(path for path in set(merged) | set(upstream)
                            if merged.get(path) != upstream.get(path))
    manifest = {
        "schema": 1,
        "repository": source_name or str(Path(source_repo).resolve()),
        "commit": new_commit,
        "modified": bool(modified_paths),
        "modified_paths": modified_paths,
    }
    write_snapshot(target, merged, manifest)
    return {
        "old_commit": old_commit,
        "new_commit": new_commit,
        "modified_paths": modified_paths,
        "destination": str(target),
    }


def main():
    parser = argparse.ArgumentParser(prog="vendor_library")
    parser.add_argument("project")
    parser.add_argument("--destination", default="Libraries/Avionics")
    parser.add_argument("--source-repo", default=Path(__file__).parents[1])
    parser.add_argument("--source-ref", default="HEAD")
    parser.add_argument("--source-name")
    parser.add_argument("--base-ref")
    args = parser.parse_args()
    try:
        result = update(args.project, args.destination, args.source_repo,
                        args.source_ref, args.source_name, args.base_ref)
    except (OSError, VendorError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
