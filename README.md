# avionics-library

KiCad symbol and footprint libraries for UCSC Rocketry avionics.

- `Avionics_Symbols.kicad_sym` — symbol library (KiCad 9.0 format)
- `Avionics_Feet.pretty/` — footprint library (`.kicad_mod` files)
- `Models/` — 3D step models
- `tools/kicad_lib_merge.py` — merge tooling (stdlib only)

## Use in KiCad

Add as project-local libraries or via Preferences → Manage Symbol/Footprint Libraries:

- Symbol library: point at `Avionics_Symbols.kicad_sym`
- Footprint library: point at `Avionics_Feet.pretty` (KiCad `.pretty` folder)

## tools/kicad_lib_merge.py

Symbol-level 3-way merge for `.kicad_sym` files plus directory merge for
`.pretty` footprint folders. Merge policy is ours-wins: common symbols keep
the ours version, deleted symbols stay deleted, symbols new on either side
are added. New 10.0-format symbols are downgraded to 9.0 unless
`--keep-modern` is given.

```sh
python3 tools/kicad_lib_merge.py list Avionics_Symbols.kicad_sym
python3 tools/kicad_lib_merge.py diff base.sym ours.sym theirs.sym [-v]
python3 tools/kicad_lib_merge.py merge base.sym ours.sym theirs.sym -o merged.sym [--keep-modern]
python3 tools/kicad_lib_merge.py normalize file.sym -o out.sym
python3 tools/kicad_lib_merge.py normalize file.sym --in-place
python3 tools/kicad_lib_merge.py fp-merge base.pretty ours.pretty theirs.pretty --dry-run
python3 tools/kicad_lib_merge.py fp-merge base.pretty ours.pretty theirs.pretty -o out.pretty [--prefer ours|theirs]
```

### Resolving a conflicted rebase/merge

When `Avionics_Symbols.kicad_sym` shows as unmerged, run from the repo root:

```sh
python3 tools/kicad_lib_merge.py resolve Avionics_Symbols.kicad_sym --stage
git rebase --continue  # or: git commit, if merging
```

`resolve` reads git stages `:1` (base), `:2` (ours), `:3` (theirs) by
default. To merge explicit files instead:

```sh
python3 tools/kicad_lib_merge.py resolve Avionics_Symbols.kicad_sym \
  --base base.sym --ours ours.sym --theirs theirs.sym
```

Verify before continuing:

```sh
python3 tools/kicad_lib_merge.py list Avionics_Symbols.kicad_sym
git diff --check
```
