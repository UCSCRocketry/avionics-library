#!/usr/bin/env python3
import argparse
from contextlib import contextmanager
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib_merge as kl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_SYM = os.path.join(ROOT, "Avionics_Symbols.kicad_sym")
LIB_PRETTY = os.path.join(ROOT, "Avionics_Feet.pretty")
LIB_MODELS = os.path.join(ROOT, "Models")

NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_\-.+]*$')
LCSC_RE = re.compile(r'^C[0-9]{5,9}$')
REF_RE = re.compile(r'^[A-Za-z]+[0-9]*$')


class ValidationError(ValueError):
    pass


def fail(msg):
    raise ValidationError(msg)

def run_git(args):
    r = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True, text=True)
    return r

def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()

def atomic_write(p, data):
    directory = os.path.dirname(os.path.abspath(p))
    fd, tmp = tempfile.mkstemp(prefix=".add-component-", dir=directory)
    try:
        mode = os.stat(p).st_mode & 0o777 if os.path.exists(p) else 0o644
        os.chmod(tmp, mode)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def write(p, s):
    atomic_write(p, s.encode("utf-8"))


def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace(
        '\n', '\\n').replace('\r', '\\r') + '"'


def validate_model_name(name):
    if (os.path.isabs(name) or os.path.basename(name) != name
            or '/' in name or '\\' in name or name in ('.', '..')):
        fail("3D model name must be a plain filename under Models/")
    if os.path.splitext(name)[1].lower() not in (".step", ".stp"):
        fail(f"3D model {name!r} must end in .step or .stp")
    return name


def parse_form(text, expected=None):
    start = kl._skip_space(text, 0, len(text))
    if start >= len(text) or text[start] != '(':
        fail("expected one S-expression")
    try:
        end = kl._expr_end(text, start)
        kind, pos = kl._atom(text, start + 1, end - 1)
    except kl.MergeError as exc:
        fail(str(exc))
    if text[end:].strip():
        fail("unexpected content after S-expression")
    if expected and kind not in expected:
        fail(f"expected {' or '.join(expected)}, found {kind!r}")
    return start, end, kind, pos


def child_forms(text, start, end, pos):
    children = []
    while True:
        pos = kl._skip_space(text, pos, end - 1)
        if pos >= end - 1:
            return children
        if text[pos] == '"':
            _, pos = kl._string(text, pos, end - 1)
            continue
        if text[pos] != '(':
            _, pos = kl._atom(text, pos, end - 1)
            continue
        child_end = kl._expr_end(text, pos, end - 1)
        kind, token_end = kl._atom(text, pos + 1, child_end - 1)
        children.append((kind, pos, child_end, token_end))
        pos = child_end


def property_entries(block):
    start, end, _, pos = parse_form(block, ("symbol",))
    try:
        _, pos = kl._string(block, pos, end - 1)
        entries = []
        for kind, child_start, child_end, token_end in child_forms(
                block, start, end, pos):
            if kind != "property":
                continue
            key, value_start = kl._string(block, token_end, child_end - 1)
            value_start = kl._skip_space(block, value_start, child_end - 1)
            value, value_end = kl._string(block, value_start, child_end - 1)
            entries.append((key, value, value_start, value_end, child_start))
        return entries
    except kl.MergeError as exc:
        fail(str(exc))

def props(block):
    result = {}
    for key, value, _, _, _ in property_entries(block):
        if key in result:
            fail(f"duplicate symbol property {key!r}")
        result[key] = value
    return result

def set_prop(block, key, value, hide):
    entries = property_entries(block)
    matches = [entry for entry in entries if entry[0] == key]
    if len(matches) > 1:
        fail(f"duplicate symbol property {key!r}")
    if matches:
        _, _, start, end, _ = matches[0]
        return block[:start] + quote(value) + block[end:]
    if hide:
        eff = '(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)'
    else:
        eff = '(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)'
    entry = f'\t\t(property {quote(key)} {quote(value)}\n\t\t\t(at 0 0 0)\n\t\t\t{eff}\n\t\t)'
    start, form_end, _, pos = parse_form(block, ("symbol",))
    _, pos = kl._string(block, pos, form_end - 1)
    children = child_forms(block, start, form_end, pos)
    at = next((child_start for kind, child_start, _, _ in children
               if kind in ("symbol", "embedded_fonts")), form_end - 1)
    line_start = block.rfind('\n', 0, at) + 1
    if not block[line_start:at].strip():
        at = line_start
    return (block[:at].rstrip() + '\n' + entry + '\n' +
            block[at:].lstrip('\r\n'))


def walk_forms(text, start, end, pos):
    for form in child_forms(text, start, end, pos):
        yield form
        _, child_start, child_end, token_end = form
        yield from walk_forms(text, child_start, child_end, token_end)


def validate_pins(block):
    start, end, _, pos = parse_form(block, ("symbol",))
    _, pos = kl._string(block, pos, end - 1)
    units = [form for form in child_forms(block, start, end, pos)
             if form[0] == "symbol"]
    pin_total = 0
    for _, unit_start, unit_end, token_end in units:
        numbers = []
        for kind, pin_start, pin_end, pin_token_end in walk_forms(
                block, unit_start, unit_end, token_end):
            if kind != "pin":
                continue
            fields = {}
            for field, _, field_end, field_token_end in child_forms(
                    block, pin_start, pin_end, pin_token_end):
                if field not in ("name", "number"):
                    continue
                try:
                    fields[field], _ = kl._string(
                        block, field_token_end, field_end - 1)
                except kl.MergeError as exc:
                    fail(str(exc))
            if not fields.get("name") or not fields.get("number"):
                fail("pin without a non-empty name and number")
            numbers.append(fields["number"])
            pin_total += 1
        duplicate = sorted(number for number in set(numbers)
                           if numbers.count(number) > 1)
        if duplicate:
            fail(f"duplicate pin numbers in a unit: {duplicate}")
    if pin_total == 0:
        fail("symbol has no pins")

def load_symbol_block(path, want):
    text = read(path)
    if "(kicad_symbol_lib" in text:
        tops, order = kl.split_tops(text)
        if want:
            if want not in tops:
                fail(f"symbol {want} not found in {path} (has: {', '.join(order)})")
            return want, tops[want]
        if len(order) != 1:
            fail(f"{path} has {len(order)} symbols, pass --symbol to select one")
        return order[0], tops[order[0]]
    _, end, _, pos = parse_form(text, ("symbol",))
    try:
        name, _ = kl._string(text, pos, end - 1)
    except kl.MergeError as exc:
        fail(str(exc))
    if want and want != name:
        fail(f"{path} contains symbol {name}, not {want}")
    return name, text if text.startswith("\n") else "\n" + text

def fix_symbol(name, block, fp_name, ref=None):
    warn = []
    if not NAME_RE.match(name) or len(name) > 64:
        fail(f"bad symbol name {name!r}: use A-Za-z0-9 _ - . +, start alnum, max 64")
    p = props(block)
    want_fp = f"Avionics_Feet:{fp_name}"
    ref_val = ref if ref is not None else (p.get("Reference") or None)
    if ref_val is None:
        fail("symbol Reference is empty; pass --reference")
    if not REF_RE.fullmatch(ref_val):
        fail("Reference must contain letters followed by optional digits")
    if p.get("Footprint") and p["Footprint"] != want_fp:
        warn.append(f"footprint property fixed to {want_fp!r}")
    if p.get("Value") and p["Value"] != name:
        warn.append(f"Value fixed to {name!r}")
    for key, val, hide in (
        ("Reference", ref_val, False),
        ("Value", name, False),
        ("Footprint", want_fp, True),
        ("LCSC Part #", p.get("LCSC Part #") or "", True),
        ("Datasheet", p.get("Datasheet") or "", True),
        ("Description", p.get("Description") or "", True),
    ):
        block = set_prop(block, key, val, hide)
    p = props(block)
    if not LCSC_RE.match(p.get("LCSC Part #", "")):
        fail("LCSC Part # is missing or invalid (must be C + 5-9 digits); pass --lcsc")
    if p.get("Footprint") != want_fp:
        fail(f"Footprint must be {want_fp}")
    validate_pins(block)
    return block, warn

def set_model_reference(text, model_reference):
    start, end, _, pos = parse_form(text, ("footprint", "module"))
    pos = kl._skip_space(text, pos, end - 1)
    if pos >= end - 1:
        fail("footprint is missing its name")
    if text[pos] == '"':
        _, pos = kl._string(text, pos, end - 1)
    else:
        _, pos = kl._atom(text, pos, end - 1)
    models = [form for form in child_forms(text, start, end, pos)
              if form[0] == "model"]
    if len(models) > 1:
        fail("footprint contains multiple 3D models")
    if models:
        _, _, model_end, token_end = models[0]
        path_start = kl._skip_space(text, token_end, model_end - 1)
        if text[path_start] == '"':
            _, path_end = kl._string(text, path_start, model_end - 1)
        else:
            _, path_end = kl._atom(text, path_start, model_end - 1)
        return text[:path_start] + quote(model_reference) + text[path_end:]
    entry = (f'\n\t(model {quote(model_reference)}\n'
             '\t\t(offset (xyz 0 0 0))\n'
             '\t\t(scale (xyz 1 1 1))\n'
             '\t\t(rotate (xyz 0 0 0))\n'
             '\t)')
    return text[:end - 1].rstrip() + entry + '\n' + text[end - 1:]


def fix_footprint(path, fp_name, model_reference):
    text = read(path)
    _, end, _, pos = parse_form(text, ("footprint", "module"))
    pos = kl._skip_space(text, pos, end - 1)
    if pos >= end - 1:
        fail("footprint is missing its name")
    try:
        if text[pos] == '"':
            found_name, _ = kl._string(text, pos, end - 1)
        else:
            found_name, _ = kl._atom(text, pos, end - 1)
    except kl.MergeError as exc:
        fail(str(exc))
    if found_name != fp_name:
        fail(f'footprint is {found_name!r}, expected {fp_name!r}')
    warn = []
    # Generation 1: (property "Reference" "...") / (property "Value" "...")
    if f'(property "Reference"' in text and f'(property "Value"' in text:
        for k, fix in (("Reference", "REF**"), ("Value", fp_name)):
            m = re.search(rf'\(property "{k}" "([^"]*)"', text)
            if not m:
                fail(f"could not parse footprint property {k!r}")
            if m.group(1) != fix:
                replacement = f'(property {quote(k)} {quote(fix)}'
                text = text[:m.start()] + replacement + text[m.end():]
                warn.append(f"footprint {k} fixed to {fix!r}")
        for k in ("Reference", "Value"):
            if f'(property "{k}"' not in text:
                fail(f"footprint missing property {k!r}")
    # Generation 2: fp_text reference "REF**" / fp_text value "name"
    elif 'fp_text reference' in text and 'fp_text value' in text:
        for k, fix in (("reference", "REF**"), ("value", fp_name)):
            m = re.search(rf'fp_text {k}\s+(?:"([^"]*)"|([^\s()]+))', text)
            if not m:
                fail(f"could not parse footprint fp_text {k}")
            value = m.group(1) if m.group(1) is not None else m.group(2)
            if value != fix:
                text = text[:m.start()] + f'fp_text {k} {quote(fix)}' + text[m.end():]
                warn.append(f"footprint {k} fixed to {fix!r}")
        for k in ("reference", "value"):
            if f'fp_text {k}' not in text:
                fail(f"footprint missing fp_text {k}")
    else:
        fail("footprint has neither (property ...) nor fp_text ... Reference/Value")
    text = set_model_reference(text, model_reference)
    parse_form(text, ("footprint", "module"))
    return text, warn

def edit_loop(name, block, fp_name, editor):
    while True:
        with tempfile.NamedTemporaryFile("w", suffix=f"-{name}.sym", delete=False) as f:
            f.write(block.strip("\n") + "\n")
            tmp = f.name
        r = subprocess.run([editor, tmp])
        if r.returncode != 0:
            os.unlink(tmp)
            fail(f"editor exited {r.returncode}, aborting (nothing written)")
        with open(tmp) as f:
            edited = f.read()
        os.unlink(tmp)
        if "(kicad_symbol_lib" in edited:
            print("error: edit only the symbol block, not a full library", file=sys.stderr)
        else:
            try:
                edited = edited.strip("\n")
                edited = edited if edited.startswith("\n") else "\n" + edited
                start, end, _, pos = parse_form(edited, ("symbol",))
                found, _ = kl._string(edited, pos, end - 1)
                if found != name:
                    fail(f"edited block must contain symbol {name}, found {found}")
                edited, _ = fix_symbol(name, edited, fp_name)
                return edited
            except (ValidationError, kl.MergeError) as exc:
                print(f"error: {exc}", file=sys.stderr)
        try:
            ans = input("re-edit symbol? [Y/n] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("", "y", "yes"):
            fail("aborted (nothing written)")

def insert_symbol(lib_text, name, block):
    tops, order = kl.split_tops(lib_text)
    if name in tops:
        fail(f"symbol {name} already exists in library")
    names = sorted(order + [name])
    if kl.is_modern(block) and not kl.is_modern(lib_text):
        block = kl.downgrade_block(block)
    return kl._compose(lib_text, [tops[n] if n in tops else block
                                  for n in names])

def target_snapshot(path):
    if os.path.islink(path):
        fail(f"refusing to replace symlink: {path}")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read(), os.stat(path).st_mode & 0o777


def restore_targets(snapshots):
    for path, snapshot in snapshots.items():
        if snapshot is None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        else:
            data, mode = snapshot
            atomic_write(path, data)
            os.chmod(path, mode)


def check_git_state(paths, allow_dirty):
    result = run_git(["rev-parse", "--is-inside-work-tree"])
    if result.returncode != 0 or result.stdout.strip() != "true":
        fail("repository is not a Git working tree")
    result = run_git(["status", "--porcelain=v1", "--"] + paths)
    if result.returncode != 0:
        fail(result.stderr.strip() or "git status failed")
    if result.stdout:
        fail("target files already have staged, unstaged, or untracked changes")
    result = run_git(["status", "--porcelain=v1"])
    if result.returncode != 0:
        fail(result.stderr.strip() or "git status failed")
    if result.stdout and not allow_dirty:
        changed = [line[3:] for line in result.stdout.splitlines() if line]
        fail(f"unrelated dirty files: {', '.join(changed)} "
             "(commit/stash or pass --allow-dirty)")


@contextmanager
def repository_lock():
    result = run_git(["rev-parse", "--git-path", "add-component.lock"])
    if result.returncode != 0:
        fail(result.stderr.strip() or "could not locate Git directory")
    path = result.stdout.strip()
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        fail("another add_component process appears to be running")
    try:
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.close(fd)
        yield
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def inspect_footprint(fp_dest, requested_editor=None):
    editor = requested_editor or os.environ.get("KICAD_PCB_EDITOR")
    command = None
    if editor:
        command = [editor, fp_dest]
    elif os.path.exists("/Applications/KiCad/PCB Editor.app"):
        command = ["open", "-W", "-a", "/Applications/KiCad/PCB Editor.app",
                   fp_dest]
    elif shutil.which("pcbnew"):
        command = [shutil.which("pcbnew"), fp_dest]
    if command is None:
        fail("KiCad PCB Editor not found; pass --pcb-editor or set "
             "KICAD_PCB_EDITOR")
    print(f"opening {fp_dest} in KiCad for inspection...")
    r = subprocess.run(command)
    if r.returncode != 0:
        fail(f"KiCad PCB Editor exited {r.returncode}")

def main():
    p = argparse.ArgumentParser(prog="add_component")
    p.add_argument("--symbol-file", required=True)
    p.add_argument("--symbol")
    p.add_argument("--footprint-file", required=True)
    p.add_argument("--footprint")
    p.add_argument("--model-file", required=True)
    p.add_argument("--model")
    p.add_argument("--model-reference")
    p.add_argument("--lcsc")
    p.add_argument("--datasheet")
    p.add_argument("--description")
    p.add_argument("--reference")
    p.add_argument("--message")
    p.add_argument("--check-only", action="store_true")
    p.add_argument("--no-commit", action="store_true")
    p.add_argument("--allow-dirty", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--edit", action="store_true")
    p.add_argument("--editor")
    p.add_argument("--inspect", action="store_true")
    p.add_argument("--pcb-editor")
    a = p.parse_args()

    for f in (a.symbol_file, a.footprint_file, a.model_file):
        if not os.path.isfile(f):
            fail(f"file not found: {f}")

    fp_name = a.footprint or os.path.splitext(os.path.basename(a.footprint_file))[0]
    if not NAME_RE.match(fp_name):
        fail(f"bad footprint name {fp_name!r}")
    if os.path.splitext(a.footprint_file)[1] != ".kicad_mod":
        fail("footprint file must end in .kicad_mod")

    model_base = a.model or os.path.basename(a.model_file)
    validate_model_name(model_base)
    model_reference = (a.model_reference or
                       f"${{KIPRJMOD}}/Models/{model_base}")

    sym_name, block = load_symbol_block(a.symbol_file, a.symbol)
    if a.reference:
        block = set_prop(block, "Reference", a.reference, False)
    if a.datasheet is not None:
        block = set_prop(block, "Datasheet", a.datasheet, True)
    if a.description is not None:
        block = set_prop(block, "Description", a.description, True)
    block = set_prop(block, "Footprint", f"Avionics_Feet:{fp_name}", True)
    if a.lcsc:
        block = set_prop(block, "LCSC Part #", a.lcsc, True)

    block, swarns = fix_symbol(sym_name, block, fp_name, a.reference)
    footprint_text, fwarns = fix_footprint(
        a.footprint_file, fp_name, model_reference)
    if swarns or fwarns:
        for w in swarns:
            print(f"fixed: {w}")
        for w in fwarns:
            print(f"fixed footprint: {w}")

    lib_text = read(LIB_SYM)
    if sym_name in kl.top_names(lib_text):
        fail(f"symbol {sym_name} already exists in library")
    fp_dest = os.path.join(LIB_PRETTY, fp_name + ".kicad_mod")
    if os.path.lexists(fp_dest) and os.path.islink(fp_dest):
        fail(f"refusing to replace symlink: {fp_dest}")
    if os.path.exists(fp_dest) and not a.force:
        ours = read(fp_dest)
        theirs = footprint_text
        if ours != theirs:
            fail(f"footprint {fp_name} already exists with different content (pass --force to overwrite)")
    model_dest = os.path.join(LIB_MODELS, model_base)
    if os.path.commonpath((os.path.realpath(LIB_MODELS),
                           os.path.realpath(model_dest))) != os.path.realpath(LIB_MODELS):
        fail("3D model destination escapes Models/")
    if os.path.lexists(model_dest) and os.path.islink(model_dest):
        fail(f"refusing to replace symlink: {model_dest}")
    with open(a.model_file, "rb") as f:
        model_bytes = f.read()
    if os.path.exists(model_dest) and not a.force:
        with open(model_dest, "rb") as f:
            have = f.read()
        if have != model_bytes:
            fail(f"model {model_base} already exists with different content (pass --force to overwrite)")

    new_lib = insert_symbol(lib_text, sym_name, block)
    if sym_name not in kl.top_names(new_lib):
        fail("merged library missing new symbol, aborting")

    if a.edit:
        editor = a.editor or os.environ.get("EDITOR") or "vi"
        block = edit_loop(sym_name, block, fp_name, editor)
        block, _ = fix_symbol(sym_name, block, fp_name, a.reference)
        new_lib = insert_symbol(lib_text, sym_name, block)
        if sym_name not in kl.top_names(new_lib):
            fail("merged library missing new symbol, aborting")

    if a.check_only:
        print(f"ok: {sym_name} + {fp_name} + {model_base}, no errors")
        return

    rel_sym = os.path.relpath(LIB_SYM, ROOT)
    rel_fp = os.path.relpath(fp_dest, ROOT)
    rel_model = os.path.relpath(model_dest, ROOT)
    paths = (rel_sym, rel_fp, rel_model)
    with repository_lock():
        check_git_state(list(paths), a.allow_dirty)
        if read(LIB_SYM) != lib_text:
            fail("symbol library changed during validation; retry")
        snapshots = {path: target_snapshot(path)
                     for path in (LIB_SYM, fp_dest, model_dest)}
        try:
            write(LIB_SYM, new_lib)
            write(fp_dest, footprint_text)
            atomic_write(model_dest, model_bytes)
            if a.inspect:
                inspect_footprint(fp_dest, a.pcb_editor)
                footprint_text, _ = fix_footprint(
                    fp_dest, fp_name, model_reference)
                write(fp_dest, footprint_text)
            result = run_git(["add", "--"] + list(paths))
            if result.returncode != 0:
                fail(result.stderr.strip() or "git add failed")
            if a.inspect or a.no_commit:
                print(f"staged: {rel_sym} {rel_fp} {rel_model}")
                if a.inspect:
                    print("commit manually with: git commit -m 'Add " +
                          sym_name + "'")
                return
            msg = a.message or f"Add {sym_name}"
            result = run_git(["commit", "-m", msg, "--"] + list(paths))
            if result.returncode != 0:
                fail((result.stderr or result.stdout).strip() or
                     "git commit failed")
            print(f"committed: {msg}")
        except BaseException:
            restore_targets(snapshots)
            run_git(["reset", "-q", "HEAD", "--"] + list(paths))
            raise

if __name__ == "__main__":
    try:
        main()
    except (ValidationError, kl.MergeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
