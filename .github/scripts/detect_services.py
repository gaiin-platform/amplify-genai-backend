#!/usr/bin/env python3
"""Work out which backend services a change affects.

Used by .github/workflows/deploy-dev.yml. It has no AWS or GitHub dependency,
so it can be run and tested on any machine:

    git diff --name-only --no-renames A..B > changed.txt
    python3 .github/scripts/detect_services.py --changed-files changed.txt

Rules (all data lives in deploy-config.json):
  * A changed file selects the service whose top-level folder contains it.
  * A change to a shared path selects every service.
  * Docs (*.md, docs/ at the root or directly inside a service) select nothing.
  * The result is always in dependency order, the order of "services" in the config.

Prints a JSON list on stdout. Explanations go to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy-config.json")


def load_config(path: str = DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    services = config["services"]
    if len(services) != len(set(services)):
        raise ValueError("deploy-config.json lists a service more than once")
    return config


def parse_changed_files(text: str) -> list[str]:
    """Accept NUL-separated (git diff -z) or newline-separated paths."""
    separator = "\0" if "\0" in text else "\n"
    return [line.strip() for line in text.split(separator) if line.strip()]


def is_ignored(path: str, config: dict) -> bool:
    """True for documentation that must never trigger a deployment."""
    lowered = path.lower()
    if any(lowered.endswith(suffix) for suffix in config.get("ignored_suffixes", [])):
        return True
    parts = path.split("/")
    ignored_dirs = config.get("ignored_dirs", [])
    # docs/** at the repo root, or <top-level folder>/docs/**
    if parts[0] in ignored_dirs and len(parts) > 1:
        return True
    if len(parts) > 2 and parts[1] in ignored_dirs:
        return True
    return False


def is_shared(path: str, config: dict) -> bool:
    for shared in config.get("shared_paths", []):
        if shared.endswith("/"):
            if path.startswith(shared):
                return True
        elif path == shared:
            return True
    return False


def select_from_changes(changed_files: list[str], config: dict) -> tuple[list[str], str]:
    """Return (services in dependency order, human-readable reason)."""
    services = config["services"]
    relevant = [path for path in changed_files if not is_ignored(path, config)]
    if not relevant:
        return [], "only documentation changed" if changed_files else "no files changed"

    shared_hits = [path for path in relevant if is_shared(path, config)]
    if shared_hits:
        return list(services), f"shared path changed ({shared_hits[0]}), selecting every service"

    touched = {path.split("/", 1)[0] for path in relevant if "/" in path}
    selected = [service for service in services if service in touched]
    if not selected:
        return [], "no service folder changed"
    return selected, "service folders changed"


def select_from_input(text: str, config: dict) -> list[str]:
    """Parse the workflow_dispatch input: 'all' or a comma-separated list."""
    services = config["services"]
    requested = [item.strip() for item in text.split(",") if item.strip()]
    if not requested:
        raise ValueError("no services given. Use 'all' or a comma-separated list of service folders")
    if [item.lower() for item in requested] == ["all"]:
        return list(services)
    unknown = [item for item in requested if item not in services]
    if unknown:
        raise ValueError(
            "unknown service(s): " + ", ".join(unknown) + ". Known services: " + ", ".join(services)
        )
    return [service for service in services if service in requested]


def find_unregistered(repo_root: str, config: dict) -> list[str]:
    """Top-level folders that have a serverless.yml but are not in the config."""
    known = set(config["services"]) | set(config.get("excluded_folders", {}))
    found = []
    for entry in sorted(os.listdir(repo_root)):
        if entry.startswith("."):
            continue
        if os.path.isfile(os.path.join(repo_root, entry, "serverless.yml")) and entry not in known:
            found.append(entry)
    return found


def find_missing(repo_root: str, config: dict) -> list[str]:
    """Services in the config whose folder has no serverless.yml."""
    return [
        service
        for service in config["services"]
        if not os.path.isfile(os.path.join(repo_root, service, "serverless.yml"))
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--repo-root", help="check that the config matches the service folders in this checkout")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--changed-files", help="file with changed paths, or - for stdin")
    source.add_argument("--services", help="'all' or a comma-separated list (manual runs)")
    args = parser.parse_args(argv)

    config = load_config(args.config)

    if args.repo_root:
        unregistered = find_unregistered(args.repo_root, config)
        missing = find_missing(args.repo_root, config)
        if unregistered:
            print(
                "error: these folders have a serverless.yml but are not registered in "
                f"{os.path.basename(args.config)}: {', '.join(unregistered)}. "
                'Add each one to "services" (in dependency order) or to "excluded_folders".',
                file=sys.stderr,
            )
        if missing:
            print(
                f"error: these services are in {os.path.basename(args.config)} but have no "
                f"serverless.yml: {', '.join(missing)}.",
                file=sys.stderr,
            )
        if unregistered or missing:
            return 2

    if args.services is not None:
        try:
            selected = select_from_input(args.services, config)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        reason = "selected manually"
    else:
        if args.changed_files == "-":
            text = sys.stdin.read()
        else:
            with open(args.changed_files, encoding="utf-8", errors="surrogateescape") as handle:
                text = handle.read()
        selected, reason = select_from_changes(parse_changed_files(text), config)

    print(f"detect: {reason}", file=sys.stderr)
    print(json.dumps(selected, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
