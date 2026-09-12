#!/usr/bin/env python3
import argparse
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
PROP_RE = re.compile(r'\(property "([^"]+)" "([^"]*)"')
SUB_RE = re.compile(r'\n\t\t\(symbol "([^"]+)"')
TOP_ONE_RE = re.compile(r'^\t\(symbol "([^"]+)"', re.MULTILINE)

def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)

def run_git(args):
    r = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True, text=True)
    return r

def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()

def write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)

def props(block):
    return dict(PROP_RE.findall(block))

def set_prop(block, key, value, hide):
    pat = re.compile(r'\(property "' + re.escape(key) + r'" "[^"]*"')
    if pat.search(block):
        return pat.sub(f'(property "{key}" "{value}"', block, count=1)
    if hide:
        eff = '(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)'
    else:
        eff = '(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)'
    entry = f'\t\t(property "{key}" "{value}"\n\t\t\t(at 0 0 0)\n\t\t\t{eff}\n\t\t)'
    m = SUB_RE.search(block)
    if m:
        at = block.index(m.group(0))
        return block[:at] + '\n' + entry + block[at:]
    m2 = re.search(r'\n\t\t\(embedded_fonts', block)
    if m2:
        at = block.index(m2.group(0))
        return block[:at] + '\n' + entry + block[at:]
    return block.rstrip('\n') + '\n' + entry + '\n\t)'

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
    m = re.search(r'\(symbol "([^"]+)"', text)
    if not m:
        fail(f"no (symbol ...) found in {path}")
    name = m.group(1)
    if want and want != name:
        fail(f"{path} contains symbol {name}, not {want}")
    return name, text if text.startswith("\n") else "\n" + text

def fix_symbol(name, block, fp_name):
    warn = []
    if not NAME_RE.match(name) or len(name) > 64:
        fail(f"bad symbol name {name!r}: use A-Za-z0-9 _ - . +, start alnum, max 64")
    if block.count("(") != block.count(")"):
        fail("unbalanced parentheses in symbol block")
    p = props(block)
    want_fp = f"Avionics_Feet:{fp_name}"
    for key, val, hide in (
        ("Reference", p.get("Reference") or "U", False),
        ("Value", p.get("Value") or name, False),
        ("Footprint", p.get("Footprint") or want_fp, True),
        ("LCSC Part #", p.get("LCSC Part #") or "", True),
        ("Datasheet", p.get("Datasheet") or "", True),
        ("Description", p.get("Description") or "", True),
    ):
        block = set_prop(block, key, val, hide)
    if "Footprint" in p and p["Footprint"] != want_fp:
        warn.append(f"footprint property fixed to {want_fp!r}")
    if p.get("Reference") != "U" and "Reference" in p:
        warn.append("Reference fixed to U")
    if p.get("Value") != name and "Value" in p:
        warn.append(f"Value fixed to {name!r}")
    if not LCSC_RE.match(p.get("LCSC Part #", "")):
        fail("LCSC Part # is missing or invalid (must be C + 5-9 digits); pass --lcsc")
    if "Footprint" in p and not p["Footprint"].startswith("Avionics_Feet:"):
        fail("Footprint must start with Avionics_Feet:")
    units = SUB_RE.split(block)
    pin_total = 0
    for seg in units[1:]:
        nums = re.findall(r'\(number "([^"]+)"', seg)
        names = re.findall(r'\(name "([^"]+)"', seg)
        pin_total += len(nums)
        if len(nums) != len(names):
            fail("pin without name or number in a unit")
        if len(set(nums)) != len(nums):
            dup = sorted(n for n in set(nums) if nums.count(n) > 1)
            fail(f"duplicate pin numbers in a unit: {dup}")
        if any(not n for n in names + nums):
            fail("empty pin name or number")
    if pin_total == 0:
        fail("symbol has no pins")
    return block, warn

def fix_footprint(path, fp_name):
    text = read(path)
    if text.count("(") != text.count(")"):
        fail("unbalanced parentheses in footprint")
    first = next((l for l in text.splitlines() if l.strip()), "")
    if not (re.match(rf'^\(footprint "{re.escape(fp_name)}"', first.strip()) or
            re.match(rf'^\(footprint {re.escape(fp_name)}(?: |\)|$)', first.strip()) or
            re.match(rf'^\(module "{re.escape(fp_name)}"', first.strip()) or
            re.match(rf'^\(module {re.escape(fp_name)}(?: |\)|$)', first.strip())):
        fail(f'first line is {first.strip()!r}, expected footprint "{fp_name}"')
    warn = []
    # Generation 1: (property "Reference" "...") / (property "Value" "...")
    if f'(property "Reference"' in text and f'(property "Value"' in text:
        for k, fix in (("Reference", "REF**"), ("Value", fp_name)):
            m = re.search(rf'\(property "{k}" "([^"]*)"', text)
            if m and m.group(1) != fix:
                text = text.replace(f'(property "{k}" "{m.group(1)}"', f'(property "{k}" "{fix}"', 1)
                warn.append(f"footprint {k} fixed to {fix!r}")
        for k in ("Reference", "Value"):
            if f'(property "{k}"' not in text:
                fail(f"footprint missing property {k!r}")
    # Generation 2: fp_text reference "REF**" / fp_text value "name"
    elif 'fp_text reference' in text and 'fp_text value' in text:
        for k, fix in (("reference", "REF**"), ("value", fp_name)):
            m = re.search(rf'fp_text {k} "([^"]*)"', text)
            if m and m.group(1) != fix:
                text = text.replace(f'fp_text {k} "{m.group(1)}"', f'fp_text {k} "{fix}"', 1)
                warn.append(f"footprint {k} fixed to {fix!r}")
        for k in ("reference", "value"):
            if f'fp_text {k}' not in text:
                fail(f"footprint missing fp_text {k}")
    else:
        fail("footprint has neither (property ...) nor fp_text ... Reference/Value")
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
            found = TOP_ONE_RE.findall(edited)
            if found != [name]:
                print(f"error: edited block must contain exactly symbol {name}, found {found}", file=sys.stderr)
            else:
                edited = edited.strip("\n")
                edited = edited if edited.startswith("\n") else "\n" + edited
                errs = check_symbol(name, edited, fp_name)
                if not errs:
                    return edited
                for e in errs:
                    print(f"error: {e}", file=sys.stderr)
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
    hdr_end = lib_text.index('\n\t(symbol "' + order[0] + '"')
    head = lib_text[:hdr_end].rstrip("\n") + "\n"
    names = sorted(order + [name])
    if kl.is_modern(block) and not kl.is_modern(lib_text):
        block = kl.downgrade_block(block)
    parts = [head]
    for n in names:
        parts.append(tops[n] if n in tops else block)
    body = ""
    for q in parts:
        body += q if q.endswith("\n") else q + "\n"
    return body.rstrip("\n") + "\n)\n"

def main():
    p = argparse.ArgumentParser(prog="add_component")
    p.add_argument("--symbol-file", required=True)
    p.add_argument("--symbol")
    p.add_argument("--footprint-file", required=True)
    p.add_argument("--footprint")
    p.add_argument("--model-file", required=True)
    p.add_argument("--model")
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
    if os.path.splitext(model_base)[1].lower() not in (".step", ".stp"):
        fail(f"3D model {model_base!r} must end in .step or .stp")

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

    block, swarns = fix_symbol(sym_name, block, fp_name)
    footprint_text, fwarns = fix_footprint(a.footprint_file, fp_name)
    if swarns or fwarns:
        for w in swarns:
            print(f"fixed: {w}")
        for w in fwarns:
            print(f"fixed footprint: {w}")

    lib_text = read(LIB_SYM)
    if re.search(r'\n\t\(symbol "' + re.escape(sym_name) + r'"', lib_text):
        fail(f"symbol {sym_name} already exists in library")
    fp_dest = os.path.join(LIB_PRETTY, fp_name + ".kicad_mod")
    if os.path.exists(fp_dest) and not a.force:
        ours = read(fp_dest)
        theirs = footprint_text
        if ours != theirs:
            fail(f"footprint {fp_name} already exists with different content (pass --force to overwrite)")
    model_dest = os.path.join(LIB_MODELS, model_base)
    if os.path.exists(model_dest) and not a.force:
        with open(model_dest, "rb") as f:
            have = f.read()
        with open(a.model_file, "rb") as f:
            want_bytes = f.read()
        if have != want_bytes:
            fail(f"model {model_base} already exists with different content (pass --force to overwrite)")

    new_lib = insert_symbol(lib_text, sym_name, block)
    if new_lib.count("(") != new_lib.count(")"):
        fail("merged library has unbalanced parentheses, aborting")
    if sym_name not in kl.top_names(new_lib):
        fail("merged library missing new symbol, aborting")

    if a.edit:
        editor = a.editor or os.environ.get("EDITOR") or "vi"
        block = edit_loop(sym_name, block, fp_name, editor)
        block, _ = fix_symbol(sym_name, block, fp_name)
        new_lib = insert_symbol(lib_text, sym_name, block)
        if new_lib.count("(") != new_lib.count(")"):
            fail("merged library has unbalanced parentheses, aborting")
        if sym_name not in kl.top_names(new_lib):
            fail("merged library missing new symbol, aborting")

    if a.check_only:
        print(f"ok: {sym_name} + {fp_name} + {model_base}, no errors")
        return

    r = run_git(["status", "--porcelain"])
    if r.returncode != 0:
        fail("git status failed")
    allowed = {
        os.path.relpath(LIB_SYM, ROOT),
        os.path.relpath(fp_dest, ROOT),
        os.path.relpath(model_dest, ROOT),
    }
    dirty = [l[3:] for l in r.stdout.splitlines() if l.strip()]
    other = [d for d in dirty if d not in allowed]
    if other and not a.allow_dirty and not a.no_commit:
        fail(f"unrelated dirty files: {', '.join(other)} (commit/stash or pass --allow-dirty)")

    write(LIB_SYM, new_lib)
    write(fp_dest, footprint_text)
    shutil.copy2(a.model_file, model_dest)

    rel_sym = os.path.relpath(LIB_SYM, ROOT)
    rel_fp = os.path.relpath(fp_dest, ROOT)
    rel_model = os.path.relpath(model_dest, ROOT)
    r = run_git(["add", rel_sym, rel_fp, rel_model])
    if r.returncode != 0:
        fail(r.stderr.strip() or "git add failed")
    if a.no_commit:
        print(f"staged: {rel_sym} {rel_fp} {rel_model}")
        return
    msg = a.message or f"Add {sym_name}"
    r = run_git(["commit", "-m", msg, rel_sym, rel_fp, rel_model])
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip() or "git commit failed")
    print(f"committed: {msg}")

if __name__ == "__main__":
    main()
