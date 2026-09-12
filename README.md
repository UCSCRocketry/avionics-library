# avionics-library

KiCad symbol and footprint libraries for UCSC Rocketry avionics.

- `Avionics_Symbols.kicad_sym` — symbol library (KiCad 9.0 format)
- `Avionics_Feet.pretty/` — footprint library (`.kicad_mod` files)
- `Models/` — 3D step models
- `tools/kicad_lib_merge.py` — merge tooling (stdlib only)
- `tools/add_component.py` — add one component (symbol + footprint + 3D model) with validation, one commit

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

## tools/add_component.py

Adds one component per commit: a symbol, a footprint, and a 3D model.
The symbol is inserted alphabetically into `Avionics_Symbols.kicad_sym`,
the footprint is copied to `Avionics_Feet.pretty/<name>.kicad_mod`, the
model is copied to `Models/`, then all three are committed together.

Required checks, all must pass:

- Symbol name matches `A-Za-z0-9 _ - . +`, starts alnum, max 64 chars,
  and does not already exist in the library.
- Symbol has `Reference`, `Value`, `Footprint`, `LCSC Part #`,
  `Datasheet`, and `Description` properties.
- `LCSC Part #` matches `C` + 5-9 digits (e.g. `C367054`).
- `Footprint` is `Avionics_Feet:<name>` and matches the footprint file,
  whose `(footprint ...)` header matches its filename, whose `Reference`
  is `REF**`, and whose `Value` equals the footprint name.

On detection, the tool auto-fixes: missing/empty symbol properties are
filled with defaults (`Reference` → `U`, `Value` → symbol name,
`Footprint` → `Avionics_Feet:<name>`, etc.), the footprint
`Reference` is set to `REF**`, and the footprint `Value` is set to
the footprint name. Truly unfixable issues (bad symbol name, invalid
`LCSC Part #`, unbalanced parentheses, no pins, duplicate component)
abort before anything is written.
- Footprint file ends in `.kicad_mod`; 3D model ends in `.step`/`.stp`.
- Parentheses balance; every pin has a name and number; pin numbers are
  unique within each unit; the symbol has at least one pin.

```sh
python3 tools/add_component.py \
  --symbol-file NEW.sym [--symbol NAME] \
  --footprint-file NEW.kicad_mod [--footprint FP_NAME] \
  --model-file MODEL.step [--model MODEL_NAME] \
  --lcsc C1234567 \
  [--datasheet URL] [--description TEXT] [--reference U] \
  [--message "Add NAME"] [--check-only] [--no-commit] [--allow-dirty] [--force] \
  [--edit] [--editor EDITOR]
```

`--edit` opens the new symbol block in `$EDITOR` (or `--editor`, default
`vi`) before anything is written. Edits are re-validated; on errors you
can re-edit or abort, and the tree is left untouched on abort. The edited
block must still be exactly the same symbol name.

`--symbol-file` accepts a full `.kicad_sym` library (picks `--symbol`,
or the single symbol it contains) or a raw `(symbol ...)` snippet.
`--lcsc`, `--footprint`, `--datasheet`, `--description`, `--reference`
patch the symbol when given. `--check-only` validates without changing
anything. Default is to commit immediately (`Add NAME`); `--no-commit`
stages only. Unrelated dirty files abort the commit unless `--allow-dirty`.
