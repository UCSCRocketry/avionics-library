#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys

HDR_RE = re.compile(r'\(version (\d+)\)')
GEN_RE = re.compile(r'\(generator_version "([^"]+)"\)')
TOP_RE = re.compile(r'\n\t\(symbol "([^"]+)"')
PIN_RE = re.compile(r'\(name "([^"]+)".*?\(number "([^"]+)"', re.DOTALL)
PROP_HIDE_RE = re.compile(
    r'\n\t\t\t\(hide yes\)\n\t\t\t\(effects(\n\t\t\t\t\(font.*?\n\t\t\t\t\)\n\t\t\t\))',
    re.DOTALL,
)

def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()

def write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)

def split_tops(text):
    starts = [(m.group(1), m.start()) for m in TOP_RE.finditer(text)]
    close = text.rfind('\n)\n')
    if close == -1:
        close = len(text)
    out = {}
    order = []
    for i, (name, s) in enumerate(starts):
        e = starts[i + 1][1] if i + 1 < len(starts) else close
        s += 1
        out[name] = text[s:e]
        order.append(name)
    return out, order

def top_names(text):
    return [m.group(1) for m in TOP_RE.finditer(text)]

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

def merge_texts(base_t, ours_t, theirs_t, downgrade_new=True):
    base, _ = split_tops(base_t)
    ours, ours_order = split_tops(ours_t)
    theirs, _ = split_tops(theirs_t)
    modern_ours = is_modern(ours_t)
    names = list(ours_order)
    for n in theirs:
        if n not in ours and n not in base:
            names.append(n)
    names.sort(key=lambda s: s)
    seen = set()
    ordered = [n for n in names if not (n in seen or seen.add(n))]
    hdr_end = ours_t.index('\n\t(symbol "' + ours_order[0] + '"')
    head = ours_t[:hdr_end].rstrip('\n') + '\n'
    parts = [head]
    for n in ordered:
        if n in ours:
            parts.append(ours[n])
        else:
            b = theirs[n]
            if downgrade_new and not modern_ours and is_modern(b):
                b = downgrade_block(b)
            parts.append(b)
    body = ''
    for p in parts:
        body += p if p.endswith('\n') else p + '\n'
    return body.rstrip('\n') + '\n)\n'

def cmd_list(a):
    for n in top_names(read(a.file)):
        print(n)

def cmd_diff(a):
    bt, ot, tt = read(a.base), read(a.ours), read(a.theirs)
    b, _ = split_tops(bt)
    o, _ = split_tops(ot)
    t, _ = split_tops(tt)
    for n in sorted(set(o) | set(t)):
        po, pt = pins(o[n]) if n in o else None, pins(t[n]) if n in t else None
        if po is None:
            print(f"+ {n} (only theirs, {len(pt)} pins)")
        elif pt is None:
            print(f"- {n} (only ours, {len(po)} pins)")
        elif po != pt:
            print(f"M {n} ours:{len(po)} theirs:{len(pt)}")
            if a.verbose:
                print(f"  ours: {po}")
                print(f"  theirs: {pt}")
        elif a.verbose:
            print(f"= {n}")

def cmd_merge(a):
    out = merge_texts(read(a.base), read(a.ours), read(a.theirs),
                      downgrade_new=not a.keep_modern)
    if a.out:
        write(a.out, out)
    else:
        sys.stdout.write(out)

def cmd_normalize(a):
    t = read(a.file)
    tops, order = split_tops(t)
    hdr_end = t.index('\n\t(symbol "' + order[0] + '"')
    head = t[:hdr_end].rstrip('\n') + '\n'
    head = re.sub(r'\(version \d+\)', '(version 20241209)', head)
    head = re.sub(r'\(generator_version "[^"]+"\)', '(generator_version "9.0")', head)
    parts = [head]
    for n in order:
        parts.append(downgrade_block(tops[n]))
    body = ''
    for p in parts:
        body += p if p.endswith('\n') else p + '\n'
    s = body.rstrip('\n') + '\n)\n'
    if a.in_place:
        write(a.file, s)
    elif a.out:
        write(a.out, s)
    else:
        sys.stdout.write(s)

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()[:12]

def cmd_fp_merge(a):
    mods = lambda d: {f for f in os.listdir(d) if f.endswith('.kicad_mod')} if os.path.isdir(d) else set()
    bo, oo, to = mods(a.base), mods(a.ours), mods(a.theirs)
    only_ours = sorted(oo - to)
    only_theirs = sorted(to - oo)
    both = sorted(oo & to)
    changed = []
    for f in both:
        ho = sha(os.path.join(a.ours, f))
        ht = sha(os.path.join(a.theirs, f))
        hb = sha(os.path.join(a.base, f)) if f in bo else None
        if ho != ht and (hb is None or ht != hb or ho != hb):
            changed.append((f, hb, ho, ht))
    print(f"only-ours: {only_ours}")
    print(f"only-theirs: {only_theirs}")
    print(f"both-modified: {[f for f, _, _, _ in changed]}")
    if a.out and not a.dry_run:
        os.makedirs(a.out, exist_ok=True)
        for d in (a.ours, a.theirs):
            for f in mods(d):
                src = os.path.join(d, f)
                dst = os.path.join(a.out, f)
                if f in dict((c[0], c) for c in changed):
                    src = os.path.join(a.ours, f) if a.prefer == 'ours' else os.path.join(a.theirs, f)
                    shutil.copy2(src, dst)
                elif not os.path.exists(dst):
                    shutil.copy2(src, dst)
        for f in only_theirs:
            shutil.copy2(os.path.join(a.theirs, f), os.path.join(a.out, f))
        print(f"wrote {a.out}")

def git_stage(ref, path):
    r = subprocess.run(['git', 'show', f'{ref}:{path}'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr.strip())
    return r.stdout

def cmd_resolve(a):
    if a.base and a.ours and a.theirs:
        b, o, t = read(a.base), read(a.ours), read(a.theirs)
    else:
        b = git_stage(':1', a.file)
        o = git_stage(':2', a.file)
        t = git_stage(':3', a.file)
    out = merge_texts(b, o, t, downgrade_new=not a.keep_modern)
    write(a.file, out)
    print(f"wrote {a.file}: {len(top_names(out))} symbols {header(out)}")
    if a.stage:
        subprocess.run(['git', 'add', a.file], check=True)

def main():
    p = argparse.ArgumentParser(prog='kicad_lib_merge')
    s = p.add_subparsers(dest='cmd', required=True)
    q = s.add_parser('list')
    q.add_argument('file')
    q.set_defaults(fn=cmd_list)
    q = s.add_parser('diff')
    q.add_argument('base')
    q.add_argument('ours')
    q.add_argument('theirs')
    q.add_argument('-v', '--verbose', action='store_true')
    q.set_defaults(fn=cmd_diff)
    q = s.add_parser('merge')
    q.add_argument('base')
    q.add_argument('ours')
    q.add_argument('theirs')
    q.add_argument('-o', '--out')
    q.add_argument('--keep-modern', action='store_true')
    q.set_defaults(fn=cmd_merge)
    q = s.add_parser('normalize')
    q.add_argument('file')
    q.add_argument('-o', '--out')
    q.add_argument('--in-place', action='store_true')
    q.set_defaults(fn=cmd_normalize)
    q = s.add_parser('fp-merge')
    q.add_argument('base')
    q.add_argument('ours')
    q.add_argument('theirs')
    q.add_argument('-o', '--out')
    q.add_argument('--prefer', choices=['ours', 'theirs'], default='ours')
    q.add_argument('--dry-run', action='store_true')
    q.set_defaults(fn=cmd_fp_merge)
    q = s.add_parser('resolve')
    q.add_argument('file')
    q.add_argument('--base')
    q.add_argument('--ours')
    q.add_argument('--theirs')
    q.add_argument('--keep-modern', action='store_true')
    q.add_argument('--stage', action='store_true')
    q.set_defaults(fn=cmd_resolve)
    a = p.parse_args()
    a.fn(a)

if __name__ == '__main__':
    main()
