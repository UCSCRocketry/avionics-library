#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vendor_library


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / ".github" / "avionics-projects.json"
LIBRARY_REPOSITORY = "UCSCRocketry/avionics-library"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class SyncError(ValueError):
    pass


def run(command, cwd=None, env=None, check=True):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True)
    if check and result.returncode != 0:
        raise SyncError(result.stderr.strip() or result.stdout.strip() or
                        f"command failed: {' '.join(command)}")
    return result


def load_config(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"invalid project configuration: {exc}") from exc
    if data.get("schema") != 1 or not isinstance(data.get("projects"), list):
        raise SyncError("configuration requires schema 1 and a projects list")
    projects = []
    for entry in data["projects"]:
        if not isinstance(entry, dict):
            raise SyncError("each project entry must be an object")
        repository = entry.get("repository", "")
        if not REPOSITORY_RE.fullmatch(repository):
            raise SyncError(f"invalid GitHub repository: {repository!r}")
        projects.append({
            "repository": repository,
            "branch": entry.get("branch", "main"),
            "destination": entry.get("destination", "Libraries/Avionics"),
            "base_ref": entry.get("base_ref"),
        })
    return projects


def gh_environment():
    token = os.environ.get("AVIONICS_SYNC_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SyncError("set AVIONICS_SYNC_TOKEN to a GitHub token with "
                        "project repository access")
    os.environ["GH_TOKEN"] = token
    environment = os.environ.copy()
    return environment


def pull_request(repository, branch, environment):
    result = run([
        "gh", "pr", "list", "--repo", repository, "--state", "all",
        "--head", branch, "--json", "url,state", "--limit", "1",
    ], env=environment)
    values = json.loads(result.stdout)
    return values[0] if values else None


def remote_branch(checkout, branch):
    return run(["git", "ls-remote", "--heads", "origin",
                f"refs/heads/{branch}"], cwd=checkout).stdout.strip()


def available_branch(checkout, branch):
    if not remote_branch(checkout, branch):
        return branch
    return f"{branch}-retry-{secrets.token_hex(3)}"


def require_unchanged_branch(checkout, branch, expected):
    output = run(["git", "ls-remote", "origin", f"refs/heads/{branch}"],
                 cwd=checkout).stdout.strip()
    actual = output.split()[0] if output else None
    if actual != expected:
        raise SyncError(
            f"{branch} advanced during synchronization; retry safely")


def report_conflict(repository, source_commit, message, environment):
    title = f"Avionics library update blocked ({source_commit[:12]})"
    result = run([
        "gh", "issue", "list", "--repo", repository, "--state", "open",
        "--search", f'"{title}" in:title', "--json", "url", "--limit", "1",
    ], env=environment, check=False)
    if result.returncode == 0:
        values = json.loads(result.stdout)
        if values:
            return values[0]["url"]
    body = ("The automated avionics library update could not be merged.\n\n"
            f"Target library commit: `{source_commit}`\n\n"
            "```text\n" + message + "\n```\n\n"
            "A library maintainer must resolve this conflict; students should "
            "not discard either version.")
    created = run([
        "gh", "issue", "create", "--repo", repository, "--title", title,
        "--body", body,
    ], env=environment)
    return created.stdout.strip()


def contribution_key(checkout, destination, paths):
    digest = hashlib.sha256()
    root = Path(checkout) / destination
    for relative in sorted(paths):
        digest.update(relative.encode("utf-8") + b"\0")
        path = root / relative
        if path.exists():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def open_contribution(repository, project_commit, source_commit, checkout,
                      destination, modified_paths, environment, temporary):
    key = contribution_key(checkout, destination, modified_paths)
    project_name = repository.replace('/', '-').lower()
    branch = (f"automation/import-{project_name}-{key}-"
              f"{source_commit[:8]}")
    existing = pull_request(LIBRARY_REPOSITORY, branch, environment)
    if existing:
        return existing["url"]

    canonical = Path(temporary) / "canonical"
    run(["gh", "repo", "clone", LIBRARY_REPOSITORY, str(canonical), "--",
         "--branch", "main", "--single-branch"], env=environment)
    canonical_commit = run(
        ["git", "rev-parse", "HEAD"], cwd=canonical).stdout.strip()
    branch = available_branch(canonical, branch)
    run(["git", "switch", "-c", branch], cwd=canonical)
    project_files = vendor_library.snapshot_from_directory(
        Path(checkout) / destination)
    project_files = vendor_library.canonicalize_model_paths(
        project_files, destination)
    _, base = vendor_library.snapshot_from_ref(ROOT, source_commit)
    canonical_files = vendor_library.snapshot_from_directory(canonical)
    candidate = vendor_library.merge_snapshots(
        base, canonical_files, project_files)
    vendor_library.validate_snapshot(candidate)
    for relative in set(canonical_files) | set(candidate):
        target = canonical / relative
        if relative in candidate:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(candidate[relative])
        elif target.exists():
            target.unlink()
    changed = run(["git", "status", "--porcelain", "--"] +
                  list(vendor_library.MANAGED_ROOTS), cwd=canonical).stdout
    if not changed:
        return None
    run(["git", "config", "user.name", "avionics-library-bot"],
        cwd=canonical)
    run(["git", "config", "user.email",
         "avionics-library-bot@users.noreply.github.com"], cwd=canonical)
    run(["git", "add", "--"] + list(vendor_library.MANAGED_ROOTS),
        cwd=canonical)
    run(["git", "commit", "-m", f"Import library changes from {repository}"],
        cwd=canonical)
    require_unchanged_branch(canonical, "main", canonical_commit)
    run(["git", "push", "-u", "origin", branch], cwd=canonical,
        env=environment)
    body = (f"Automated import of project-local library changes from "
            f"[`{repository}@{project_commit[:12]}`]"
            f"(https://github.com/{repository}/commit/{project_commit}).\n\n"
            "Changed managed paths:\n\n" +
            "\n".join(f"- `{path}`" for path in modified_paths) +
            "\n\nThe result was structurally merged and validated. A library "
            "maintainer must review component correctness before merging.")
    created = run([
        "gh", "pr", "create", "--repo", LIBRARY_REPOSITORY,
        "--base", "main", "--head", branch,
        "--title", f"Import library changes from {repository}",
        "--body", body,
    ], cwd=canonical, env=environment)
    return created.stdout.strip()


def sync_project(project, source_commit, environment):
    repository = project["repository"]
    with tempfile.TemporaryDirectory() as temporary:
        checkout = Path(temporary) / "project"
        run(["gh", "repo", "clone", repository, str(checkout), "--",
             "--branch", project["branch"], "--single-branch"],
            env=environment)
        project_commit = run(
            ["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
        branch = (f"automation/avionics-{source_commit[:10]}-"
                  f"{project_commit[:8]}")
        existing = pull_request(repository, branch, environment)
        if existing:
            return {"repository": repository,
                    "status": existing["state"].lower(),
                    "url": existing["url"]}
        branch = available_branch(checkout, branch)
        try:
            update = vendor_library.update(
                checkout, project["destination"], ROOT, source_commit,
                LIBRARY_REPOSITORY, project["base_ref"])
        except vendor_library.VendorError as exc:
            issue = report_conflict(repository, source_commit, str(exc),
                                    environment)
            return {"repository": repository, "status": "conflict",
                    "url": issue, "error": str(exc)}

        contribution = None
        if update["modified_paths"]:
            try:
                contribution = open_contribution(
                    repository, project_commit, source_commit, checkout,
                    project["destination"], update["modified_paths"],
                    environment, temporary)
            except vendor_library.VendorError as exc:
                issue = report_conflict(repository, source_commit, str(exc),
                                        environment)
                return {"repository": repository, "status": "conflict",
                        "url": issue, "error": str(exc)}
        changed = run(["git", "status", "--porcelain", "--",
                       project["destination"]], cwd=checkout).stdout
        if not changed:
            return {"repository": repository, "status": "current",
                    "contribution": contribution}
        run(["git", "config", "user.name", "avionics-library-bot"],
            cwd=checkout)
        run(["git", "config", "user.email",
             "avionics-library-bot@users.noreply.github.com"], cwd=checkout)
        run(["git", "switch", "-c", branch], cwd=checkout)
        run(["git", "add", "--", project["destination"]], cwd=checkout)
        run(["git", "commit", "-m",
             f"Update avionics library to {source_commit[:12]}"], cwd=checkout)
        require_unchanged_branch(checkout, project["branch"], project_commit)
        run(["git", "push", "-u", "origin", branch], cwd=checkout,
            env=environment)
        modified = update["modified_paths"]
        body = ("Automated update from "
                "[UCSCRocketry/avionics-library]"
                "(https://github.com/UCSCRocketry/avionics-library).\n\n"
                f"Library commit: `{source_commit}`\n\n")
        if modified:
            body += ("The project had local library changes, which were "
                     "preserved by the three-way merge:\n\n" +
                     "\n".join(f"- `{path}`" for path in modified) + "\n\n")
        if contribution:
            body += f"Canonical library contribution: {contribution}\n\n"
        body += ("The snapshot was structurally validated before this PR was "
                 "created.")
        created = run([
            "gh", "pr", "create", "--repo", repository,
            "--base", project["branch"], "--head", branch,
            "--title", f"Update avionics library to {source_commit[:12]}",
            "--body", body,
        ], cwd=checkout, env=environment)
        return {"repository": repository, "status": "created",
                "url": created.stdout.strip(), "modified_paths": modified,
                "contribution": contribution}


def main():
    parser = argparse.ArgumentParser(prog="sync_projects")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--source-ref", default="HEAD")
    args = parser.parse_args()
    try:
        projects = load_config(args.config)
        if not projects:
            print("no avionics projects are registered")
            return
        environment = gh_environment()
        run(["gh", "auth", "setup-git"], env=environment)
        if args.source_ref == "HEAD":
            source_commit = vendor_library.latest_snapshot_commit(ROOT)
        else:
            source_commit = vendor_library.resolve_ref(ROOT, args.source_ref)
        results = []
        for project in projects:
            try:
                results.append(sync_project(project, source_commit,
                                            environment))
            except (OSError, SyncError, vendor_library.VendorError) as exc:
                results.append({"repository": project["repository"],
                                "status": "error", "error": str(exc)})
    except (OSError, SyncError, vendor_library.VendorError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(json.dumps(results, indent=2, sort_keys=True))
    if any(result["status"] in ("conflict", "error")
           for result in results):
        parser.exit(1, "one or more projects require maintainer review\n")


if __name__ == "__main__":
    main()
