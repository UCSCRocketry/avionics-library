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
`${KIPRJMOD}/avionics-library/Models/<model>` so it resolves from every project
that uses the standard submodule path. It can be overridden with
`--model-reference`.
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
symbol-to-footprint references, and required metadata. Every symbol must have
`Reference`, `Value`, `Footprint`, `Datasheet`, and `Description` values.
Purchasable components must also have a valid `LCSC Part #`; breakout modules
and assembled development boards may leave that property empty. Legacy
external footprint references and unlinked existing models are reported as
warnings.

The `SA818S` land pattern was adapted from the JLCEDA/EasyEDA Official Library
entry for C51897911, with manufacturer dimensions checked against the NiceRF
datasheet. The `H2UJ4U1H2Q0100` land pattern was adapted from the same official
library's C6569550 entry and checked against the Unictron CB501F datasheet.
Source libraries: https://lceda.cn/ and https://easyeda.com.

## Contribution workflow

`avionics-library` is the canonical source for shared symbols, footprints, and
3D models. Do not edit the library through a project repository's submodule.

To add a part:

1. Clone this repository and create a feature branch.
2. Run `tools/add_component.py` to add exactly one symbol, footprint, and 3D
   model in one commit.
3. Push the branch and open a pull request against `main`.
4. A library lead reviews the part and merges the pull request after the
   Python tests and validator pass.

Example:

```sh
python3 tools/add_component.py \
  --symbol-file NEW.sym \
  --footprint-file NEW.kicad_mod \
  --model-file MODEL.step \
  --lcsc C1234567 \
  --datasheet https://example.com/datasheet.pdf \
  --description "Part description"

python3 -m unittest discover -s tests -v
python3 tools/validate_library.py
```

Leads can use `tools/kicad_lib_merge.py resolve` when a pull request conflicts
with another symbol-library change. Conflict resolution must be followed by
the full test and validation commands above.

## Use as a project submodule

Every avionics electrical project should pin this repository at
`avionics-library`:

```sh
git submodule add https://github.com/UCSCRocketry/avionics-library.git avionics-library
git commit -m "Add avionics library submodule"
```

Clone projects with their library:

```sh
git clone --recurse-submodules https://github.com/UCSCRocketry/PROJECT.git
```

For an existing clone:

```sh
git submodule update --init --recursive
```

Project KiCad library tables should use project-relative paths:

```scheme
(lib (name "Avionics_Symbols")(type "KiCad")(uri "${KIPRJMOD}/avionics-library/Avionics_Symbols.kicad_sym")(options "")(descr ""))
```

```scheme
(lib (name "Avionics_Feet")(type "KiCad")(uri "${KIPRJMOD}/avionics-library/Avionics_Feet.pretty")(options "")(descr ""))
```

When a project needs a newer library version, update only its pinned commit:

```sh
git submodule update --remote avionics-library
git add avionics-library
git commit -m "Update avionics library"
```
