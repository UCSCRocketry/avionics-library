# avionics-library

KiCad symbol and footprint libraries for UCSC Rocketry avionics.

- `Avionics_Symbols.kicad_sym` — symbol library (KiCad 9.0 format)
- `Avionics_Feet.pretty/` — footprint library (`.kicad_mod` files)
- `Models/` — 3D step models
- `tools/kicad_lib_merge.py` — merge tooling (stdlib only)
- `tools/add_component.py` — add one component (symbol + footprint + 3D model) with validation, one commit
- `tools/vendor_library.py` — safely install or update a project-local snapshot
- `tools/sync_projects.py` — open automated update and contribution PRs

## Use in KiCad

Add as project-local libraries or via Preferences → Manage Symbol/Footprint Libraries:

- Symbol library: point at `Avionics_Symbols.kicad_sym`
- Footprint library: point at `Avionics_Feet.pretty` (KiCad `.pretty` folder)

## tools/kicad_lib_merge.py

Symbol-level 3-way merge for `.kicad_sym` files plus directory merge for
`.pretty` footprint folders. Changes made on only one side are retained,
including deletions. Matching changes are accepted and conflicting edits,
additions, or delete/edit combinations stop for review. Pass `--prefer ours`
or `--prefer theirs` to resolve genuine conflicts explicitly. New 10.0-format
constructs are downgraded to 9.0 unless `--keep-modern` is given.

```sh
python3 tools/kicad_lib_merge.py list Avionics_Symbols.kicad_sym
python3 tools/kicad_lib_merge.py diff base.sym ours.sym theirs.sym [-v]
python3 tools/kicad_lib_merge.py merge base.sym ours.sym theirs.sym -o merged.sym [--keep-modern] [--prefer ours|theirs]
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
default. It exits without changing the library when both sides changed the
same symbol differently. Review with `diff`, then rerun with `--prefer ours`
or `--prefer theirs` only when that choice is intentional. To merge explicit
files instead:

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
the footprint is copied to `Avionics_Feet.pretty/<name>.kicad_mod`, and the
model is copied to `Models/`. The footprint's model reference defaults to
`${KIPRJMOD}/Models/<model>` and can be overridden with `--model-reference`.
All three files are then staged or committed together.

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
filled with defaults (`Value` → symbol name, `Footprint` →
`Avionics_Feet:<name>`, etc.), and the footprint `Reference` is
set to `REF**` while its `Value` is set to the footprint name.
A missing/empty symbol `Reference` aborts; pass `--reference`.
- Footprint file ends in `.kicad_mod`; 3D model ends in `.step`/`.stp`.
- Parentheses balance; every pin has a name and number; pin numbers are
  unique within each unit; the symbol has at least one pin.

```sh
python3 tools/add_component.py \
  --symbol-file NEW.sym [--symbol NAME] \
  --footprint-file NEW.kicad_mod [--footprint FP_NAME] \
  --model-file MODEL.step [--model MODEL_NAME] [--model-reference KICAD_PATH] \
  --lcsc C1234567 \
  [--datasheet URL] [--description TEXT] [--reference U] \
  [--message "Add NAME"] [--check-only] [--no-commit] [--allow-dirty] [--force] \
  [--edit] [--editor EDITOR] [--inspect] [--pcb-editor PCBNEW]
```

`--inspect` writes the validated files, opens the footprint in KiCad's PCB
Editor, and waits for it to close so you can adjust the 3D model. It discovers
the macOS application or `pcbnew`; use `--pcb-editor` or
`KICAD_PCB_EDITOR` when needed. The edited footprint is revalidated before
all files are staged. Commit manually when done.

`--edit` opens the new symbol block in `$EDITOR` (or `--editor`, default
`vi`) before anything is written. Edits are re-validated; on errors you
can re-edit or abort, and the tree is left untouched on abort. The edited
block must still be exactly the same symbol name.

`--symbol-file` accepts a full `.kicad_sym` library (picks `--symbol`,
or the single symbol it contains) or a raw `(symbol ...)` snippet.
`--lcsc`, `--footprint`, `--datasheet`, `--description`, `--reference`
patch the symbol when given. `--check-only` validates without changing
anything. Default is to commit immediately (`Add NAME`); `--no-commit`
stages only. Unrelated dirty files abort any write unless `--allow-dirty`.
Managed target files must always be clean. Failed writes, staging, inspection,
or commits restore the original files.

## Validation

Run the complete test and repository checks before merging:

```sh
python3 -m unittest discover -s tests -v
python3 tools/validate_library.py
```

GitHub Actions runs both commands for pushes and pull requests. The validator
checks symbol-library structure and uniqueness, footprint syntax and names,
and symbol-to-footprint references. Legacy external footprint references and
unlinked existing models are reported as warnings.

## Project snapshots

Avionics projects can vendor a complete, versioned copy under
`Libraries/Avionics`. GitHub Actions in this repository update registered
projects through pull requests, preserve independent project-local edits, and
submit those edits back here for maintainer review. Students continue using a
normal one-repository GitHub Desktop workflow.

See [`docs/VENDORED_LIBRARIES.md`](docs/VENDORED_LIBRARIES.md) for project
registration, credentials, migration, conflict behavior, and maintainer
commands.
