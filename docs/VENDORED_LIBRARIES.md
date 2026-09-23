# Vendored Project Libraries

Avionics projects keep a complete library snapshot under
`Libraries/Avionics`. Students clone, edit, commit, and pull one ordinary Git
repository. This repository handles synchronization through GitHub Actions.

## Snapshot layout

```text
Libraries/Avionics/
  .avionics-library.json
  Avionics_Symbols.kicad_sym
  Avionics_Feet.pretty/
  Models/
```

The manifest records the exact canonical commit and whether project-local
changes remain. Do not edit the manifest manually.

Project KiCad library tables should use project-relative paths:

```scheme
(lib (name "Avionics_Symbols")(type "KiCad")(uri "${KIPRJMOD}/Libraries/Avionics/Avionics_Symbols.kicad_sym")(options "")(descr ""))
```

```scheme
(lib (name "Avionics_Feet")(type "KiCad")(uri "${KIPRJMOD}/Libraries/Avionics/Avionics_Feet.pretty")(options "")(descr ""))
```

Canonical model paths use `${KIPRJMOD}/Models/`. Snapshot generation rewrites
them to `${KIPRJMOD}/Libraries/Avionics/Models/` automatically.

## Register a project

Add an entry to `.github/avionics-projects.json`:

```json
{
  "schema": 1,
  "projects": [
    {
      "repository": "UCSCRocketry/example-avionics-project",
      "branch": "main",
      "destination": "Libraries/Avionics"
    }
  ]
}
```

For a project that already contains an unversioned library copy, specify the
canonical commit from which that copy originated:

```json
{
  "repository": "UCSCRocketry/example-avionics-project",
  "branch": "main",
  "destination": "Libraries/Avionics",
  "base_ref": "0123456789abcdef0123456789abcdef01234567"
}
```

Remove `base_ref` after the initialization PR is merged. If the old copy's
origin cannot be identified reliably, a maintainer must inspect the initial
merge rather than guessing a base.

## GitHub credentials

Create an organization bot token or GitHub App credential named
`AVIONICS_SYNC_TOKEN` in this repository's Actions secrets. It needs access to
this repository and every registered project, with these permissions:

- Contents: read and write
- Pull requests: read and write
- Issues: read and write
- Metadata: read

The automation uses the token only inside the protected `main` workflow. It is
not exposed to pull-request workflows.

Protect `main` in this repository and each project. Require update branches to
be current before merging and require maintainer review for automation PRs.
When a project branch advances, the next scheduled run creates a newly merged
update rather than relying on a stale textual Git merge.

## Automated updates

`.github/workflows/sync-projects.yml` runs when canonical library files change
on protected `main`, and once per day. For every registered project it:

1. Reads the project's snapshot manifest.
2. Loads that exact canonical commit as the merge base.
3. Merges the project snapshot with current canonical files.
4. Preserves independent project-local changes.
5. Validates the complete result.
6. Opens a project update PR.

If the same symbol, footprint, or model changed differently on both sides, the
project is left untouched and an issue is opened for a library maintainer.

## Project-local changes

Students may edit the snapshot in KiCad and commit it with their project work.
During the next scheduled scan, automation:

1. Detects files that differ from the recorded canonical commit.
2. Merges them against current canonical `main`.
3. Opens a PR in this repository for maintainer review.
4. Links that contribution from the project update PR.
5. Propagates the accepted canonical version to all projects after merge.

The automation only sees changes pushed to a project's configured branch. It
cannot and must not alter uncommitted files on a student's computer. GitHub
Desktop remains responsible for prompting students to commit before pulling.

## Maintainer commands

Initialize or update a local project checkout without GitHub automation:

```sh
python3 tools/vendor_library.py /path/to/project
```

Migrate an existing unversioned snapshot:

```sh
python3 tools/vendor_library.py /path/to/project --base-ref <canonical-commit>
```

Run synchronization manually from a trusted `main` checkout with
`AVIONICS_SYNC_TOKEN` set:

```sh
python3 tools/sync_projects.py
```

These are maintainer operations. Students do not need to run them.
Local manual synchronization also requires the GitHub CLI (`gh`); hosted
GitHub Actions runners already provide it.
