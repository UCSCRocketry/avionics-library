#!/usr/bin/env python3
import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HDR_RE = re.compile(r'\(version (\d+)\)')
GEN_RE = re.compile(r'\(generator_version "([^"]+)"\)')
PIN_RE = re.compile(r'\(name "([^"]+)".*?\(number "([^"]+)"', re.DOTALL)
PROP_HIDE_RE = re.compile(
    r'\n\t\t\t\(hide yes\)\n\t\t\t\(effects(\n\t\t\t\t\(font.*?\n\t\t\t\t\)\n\t\t\t\))',
    re.DOTALL,
)


class MergeError(ValueError):
    pass


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def atomic_write(p, s):
    directory = os.path.dirname(os.path.abspath(p)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".kicad-merge-", dir=directory)
    try:
        mode = os.stat(p).st_mode & 0o777 if os.path.exists(p) else 0o644
        os.chmod(tmp, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            fd = None
            f.write(s)
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
    atomic_write(p, s)


def _skip_space(text, pos, end):
    while pos < end and text[pos].isspace():
        pos += 1
    return pos


def _atom(text, pos, end):
    pos = _skip_space(text, pos, end)
    start = pos
    while pos < end and not text[pos].isspace() and text[pos] not in '()':
        pos += 1
    if pos == start:
        raise MergeError(f"expected atom at offset {pos}")
    return text[start:pos], pos


def _string(text, pos, end):
    pos = _skip_space(text, pos, end)
    if pos >= end or text[pos] != '"':
        raise MergeError(f"expected quoted string at offset {pos}")
    pos += 1
    out = []
    while pos < end:
        ch = text[pos]
        if ch == '"':
            return ''.join(out), pos + 1
        if ch == '\\':
            pos += 1
            if pos >= end:
                raise MergeError("unterminated escape in string")
            ch = text[pos]
        out.append(ch)
        pos += 1
    raise MergeError("unterminated string")


def _expr_end(text, start, limit=None):
    if start >= len(text) or text[start] != '(':
        raise MergeError(f"expected '(' at offset {start}")
    limit = len(text) if limit is None else limit
    depth = 0
    quoted = False
    escaped = False
    for pos in range(start, limit):
        ch = text[pos]
        if quoted:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return pos + 1
            if depth < 0:
                break
    if quoted:
        raise MergeError("unterminated string")
    raise MergeError("unbalanced parentheses")


def _parse_library(text):
    start = _skip_space(text, 0, len(text))
    if start >= len(text) or text[start] != '(':
        raise MergeError("library must contain one kicad_symbol_lib expression")
    end = _expr_end(text, start)
    if text[end:].strip():
        raise MergeError("unexpected content after library expression")
    root, pos = _atom(text, start + 1, end - 1)
    if root != 'kicad_symbol_lib':
        raise MergeError(f"expected kicad_symbol_lib root, found {root!r}")

    blocks = {}
    order = []
    spans = []
    while True:
        pos = _skip_space(text, pos, end - 1)
        if pos >= end - 1:
            break
        if text[pos] != '(':
            raise MergeError(f"unexpected atom at offset {pos}")
        child_end = _expr_end(text, pos, end - 1)
        kind, token_end = _atom(text, pos + 1, child_end - 1)
        if kind == 'symbol':
            name, _ = _string(text, token_end, child_end - 1)
            if name in blocks:
                raise MergeError(f"duplicate top-level symbol {name!r}")
            line_start = text.rfind('\n', 0, pos) + 1
            block_start = (line_start if text[line_start:pos].strip() == ''
                           else pos)
            blocks[name] = text[block_start:child_end]
            order.append(name)
            spans.append((block_start, child_end))
        pos = child_end

    insert = end - 1
    prefix_end = spans[0][0] if spans else insert
    suffix_start = spans[-1][1] if spans else insert
    return blocks, order, text[:prefix_end], text[suffix_start:]


def split_tops(text):
    blocks, order, _, _ = _parse_library(text)
    return blocks, order


def top_names(text):
    return split_tops(text)[1]


def pins(block):
    return sorted((num, name) for name, num in PIN_RE.findall(block))


def header(text):
    v = HDR_RE.search(text)
    g = GEN_RE.search(text)
    return (v.group(1) if v else "?", g.group(1) if g else "?")


def downgrade_block(block):
    block = re.sub(r'\n\t\t\(in_pos_files yes\)', '', block)
    block = re.sub(r'\n\t\t\(duplicate_pin_numbers_are_jumpers no\)', '', block)
    block = re.sub(r'\n\t\t\t\(show_name no\)', '', block)
    block = re.sub(r'\n\t\t\t\(do_not_autoplace no\)', '', block)

    def repl(m):
        inner = m.group(1)
        tail = '\n\t\t\t)'
        body = inner[:-len(tail)] + '\n\t\t\t\t(hide yes)' + tail
        return '\n\t\t\t(effects' + body

    block, _ = PROP_HIDE_RE.subn(repl, block)
    return block


def is_modern(text):
    return 'in_pos_files' in text or 'do_not_autoplace' in text


def _choose(name, base, ours, theirs, prefer=None):
    # Absence is a value too, so deletes merge like every other edit.
    if ours == theirs:
        return ours, 'matching'
    if ours == base:
        return theirs, 'theirs'
    if theirs == base:
        return ours, 'ours'
    if prefer == 'ours':
        return ours, 'preferred ours'
    if prefer == 'theirs':
        return theirs, 'preferred theirs'
    raise MergeError(f"conflict for {name!r}: both sides diverged from base")


def _compose_parts(prefix, suffix, selected):
    out = prefix.rstrip() + '\n'
    for block in selected:
        out += block.strip('\r\n') + '\n'
    out += suffix.lstrip('\r\n')
    if not out.endswith('\n'):
        out += '\n'
    _parse_library(out)
    return out


def _compose(template, selected):
    _, _, prefix, suffix = _parse_library(template)
    return _compose_parts(prefix, suffix, selected)


def merge_texts(base_t, ours_t, theirs_t, downgrade_new=True, prefer=None):
    base, _, base_prefix, base_suffix = _parse_library(base_t)
    ours, _, ours_prefix, ours_suffix = _parse_library(ours_t)
    theirs, _, theirs_prefix, theirs_suffix = _parse_library(theirs_t)
    prefix, _ = _choose("library header", base_prefix, ours_prefix,
                        theirs_prefix, prefer)
    suffix, _ = _choose("library footer", base_suffix, ours_suffix,
                        theirs_suffix, prefer)
    selected = []
    for name in sorted(set(base) | set(ours) | set(theirs)):
        block, _ = _choose(name, base.get(name), ours.get(name),
                           theirs.get(name), prefer)
        if block is None:
            continue
        if (downgrade_new and not is_modern(ours_t) and is_modern(block)):
            block = downgrade_block(block)
        selected.append(block)
    return _compose_parts(prefix, suffix, selected)


def _path_overlap(output, inputs, directories=False):
    out = os.path.realpath(os.path.abspath(output))
    for item in inputs:
        inp = os.path.realpath(os.path.abspath(item))
        if out == inp:
            return item
        if directories and (out.startswith(inp + os.sep) or
                            inp.startswith(out + os.sep)):
            return item
        try:
            if os.path.exists(output) and os.path.samefile(output, item):
                return item
        except OSError:
            pass
    return None


def _require_distinct_output(output, inputs, directories=False):
    if not output:
        return
    overlap = _path_overlap(output, inputs, directories)
    if overlap:
        raise MergeError(f"output {output!r} overlaps input {overlap!r}")


def cmd_list(a):
    for name in top_names(read(a.file)):
        print(name)


def _operation(base, side):
    if base is None:
        return 'unchanged' if side is None else 'add'
    if side is None:
        return 'delete'
    return 'unchanged' if side == base else 'change'


def cmd_diff(a):
    bt, ot, tt = read(a.base), read(a.ours), read(a.theirs)
    base, _ = split_tops(bt)
    ours, _ = split_tops(ot)
    theirs, _ = split_tops(tt)
    for name in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(name), ours.get(name), theirs.get(name)
        oop, top = _operation(b, o), _operation(b, t)
        if o == t:
            state = 'unchanged' if o == b else 'matching edits'
        elif o == b:
            state = f'theirs {top}'
        elif t == b:
            state = f'ours {oop}'
        else:
            state = f'conflict (ours {oop}, theirs {top})'
        print(f"{name}: {state}")
        if a.verbose:
            for label, value in (("ours", o), ("theirs", t)):
                diff = difflib.unified_diff(
                    (b or '').splitlines(), (value or '').splitlines(),
                    fromfile=f"base/{name}", tofile=f"{label}/{name}",
                    lineterm='')
                for line in diff:
                    print(line)


def cmd_merge(a):
    _require_distinct_output(a.out, (a.base, a.ours, a.theirs))
    out = merge_texts(read(a.base), read(a.ours), read(a.theirs),
                      downgrade_new=not a.keep_modern, prefer=a.prefer)
    if a.out:
        atomic_write(a.out, out)
    else:
        sys.stdout.write(out)


def cmd_normalize(a):
    if a.in_place and a.out:
        raise MergeError("normalize accepts only one of --in-place and --out")
    if a.out:
        _require_distinct_output(a.out, (a.file,))
    text = read(a.file)
    tops, order, prefix, suffix = _parse_library(text)
    prefix = re.sub(r'\(version \d+\)', '(version 20241209)', prefix)
    prefix = re.sub(r'\(generator_version "[^"]+"',
                    '(generator_version "9.0"', prefix)
    normalized = prefix.rstrip() + '\n'
    for name in order:
        normalized += downgrade_block(tops[name]).strip('\r\n') + '\n'
    normalized += suffix.lstrip('\r\n')
    if not normalized.endswith('\n'):
        normalized += '\n'
    _parse_library(normalized)
    if a.in_place:
        atomic_write(a.file, normalized)
    elif a.out:
        atomic_write(a.out, normalized)
    else:
        sys.stdout.write(normalized)


def _footprints(directory):
    if not os.path.isdir(directory):
        raise MergeError(f"footprint input is not a directory: {directory}")
    result = {}
    for name in os.listdir(directory):
        if not name.endswith('.kicad_mod'):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            raise MergeError(f"footprint is not a regular file: {path}")
        with open(path, 'rb') as f:
            result[name] = f.read()
    return result


def _atomic_directory(output, files):
    parent = os.path.dirname(os.path.abspath(output)) or '.'
    tmp = tempfile.mkdtemp(prefix='.kicad-merge-', dir=parent)
    backup = None
    try:
        for name, data in files.items():
            with open(os.path.join(tmp, name), 'wb') as f:
                f.write(data)
        if os.path.lexists(output):
            backup = tempfile.mkdtemp(prefix='.kicad-old-', dir=parent)
            os.rmdir(backup)
            os.replace(output, backup)
        try:
            os.replace(tmp, output)
            tmp = None
        except BaseException:
            if backup is not None:
                os.replace(backup, output)
                backup = None
            raise
        if backup is not None:
            if os.path.isdir(backup) and not os.path.islink(backup):
                shutil.rmtree(backup)
            else:
                os.unlink(backup)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def cmd_fp_merge(a):
    _require_distinct_output(a.out, (a.base, a.ours, a.theirs), True)
    base = _footprints(a.base)
    ours = _footprints(a.ours)
    theirs = _footprints(a.theirs)
    selected = {}
    conflicts = []
    for name in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(name), ours.get(name), theirs.get(name)
        oop, top = _operation(b, o), _operation(b, t)
        try:
            value, source = _choose(name, b, o, t, a.prefer)
        except MergeError:
            conflicts.append(name)
            print(f"{name}: conflict (ours {oop}, theirs {top})")
            continue
        print(f"{name}: {source} (ours {oop}, theirs {top})")
        if value is not None:
            selected[name] = value
    if conflicts:
        raise MergeError("footprint conflicts: " + ', '.join(conflicts))
    if a.out and not a.dry_run:
        _atomic_directory(a.out, selected)
        print(f"wrote {a.out}")


def git_stage(ref, path):
    result = subprocess.run(['git', 'show', f'{ref}:{path}'],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise MergeError(result.stderr.strip())
    return result.stdout


def cmd_resolve(a):
    explicit = (a.base, a.ours, a.theirs)
    if any(explicit) and not all(explicit):
        raise MergeError("resolve requires all of --base, --ours, and --theirs")
    if all(explicit):
        _require_distinct_output(a.file, explicit)
        b, o, t = read(a.base), read(a.ours), read(a.theirs)
    else:
        b = git_stage(':1', a.file)
        o = git_stage(':2', a.file)
        t = git_stage(':3', a.file)
    out = merge_texts(b, o, t, downgrade_new=not a.keep_modern,
                      prefer=a.prefer)
    atomic_write(a.file, out)
    print(f"wrote {a.file}: {len(top_names(out))} symbols {header(out)}")
    if a.stage:
        subprocess.run(['git', 'add', a.file], check=True)


def main():
    parser = argparse.ArgumentParser(prog='kicad_lib_merge')
    subparsers = parser.add_subparsers(dest='cmd', required=True)
    q = subparsers.add_parser('list')
    q.add_argument('file')
    q.set_defaults(fn=cmd_list)
    q = subparsers.add_parser('diff')
    q.add_argument('base')
    q.add_argument('ours')
    q.add_argument('theirs')
    q.add_argument('-v', '--verbose', action='store_true')
    q.set_defaults(fn=cmd_diff)
    q = subparsers.add_parser('merge')
    q.add_argument('base')
    q.add_argument('ours')
    q.add_argument('theirs')
    q.add_argument('-o', '--out')
    q.add_argument('--keep-modern', action='store_true')
    q.add_argument('--prefer', choices=['ours', 'theirs'])
    q.set_defaults(fn=cmd_merge)
    q = subparsers.add_parser('normalize')
    q.add_argument('file')
    q.add_argument('-o', '--out')
    q.add_argument('--in-place', action='store_true')
    q.set_defaults(fn=cmd_normalize)
    q = subparsers.add_parser('fp-merge')
    q.add_argument('base')
    q.add_argument('ours')
    q.add_argument('theirs')
    q.add_argument('-o', '--out')
    q.add_argument('--prefer', choices=['ours', 'theirs'])
    q.add_argument('--dry-run', action='store_true')
    q.set_defaults(fn=cmd_fp_merge)
    q = subparsers.add_parser('resolve')
    q.add_argument('file')
    q.add_argument('--base')
    q.add_argument('--ours')
    q.add_argument('--theirs')
    q.add_argument('--keep-modern', action='store_true')
    q.add_argument('--prefer', choices=['ours', 'theirs'])
    q.add_argument('--stage', action='store_true')
    q.set_defaults(fn=cmd_resolve)
    args = parser.parse_args()
    try:
        args.fn(args)
    except (MergeError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == '__main__':
    main()
