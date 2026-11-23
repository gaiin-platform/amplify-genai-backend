#!/usr/bin/env python3
"""Turn `aws cloudformation describe-change-set` JSON into a review summary.

Used by .github/workflows/deploy-dev.yml. It never calls AWS, so it can be run
and tested against saved JSON files (see tests/samples/):

    python3 .github/scripts/analyze_changeset.py analyze \
        --changeset tests/samples/remove.json --service amplify-lambda-admin

Two commands:
  analyze   One change set -> Markdown for the job summary plus a small result JSON.
  overview  All result JSON files of a run -> one table in execution order.

A resource change is flagged as destructive when
  * Action is "Remove", or
  * Action is "Modify" and Replacement is "True" or "Conditional"
    (a replacement deletes the resource and creates a new, empty one).
Resource types listed under "ignored_resource_types" in deploy-config.json are
never flagged. That list is the single place to add known noise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import quote

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy-config.json")

NO_CHANGE_MARKERS = ("didn't contain changes", "no updates are to be performed")
REPLACEMENT_RISKY = ("True", "Conditional")


def load_ignored_types(config_path: str = DEFAULT_CONFIG) -> list[str]:
    with open(config_path, encoding="utf-8") as handle:
        return list(json.load(handle).get("ignored_resource_types", []))


def is_destructive(change: dict) -> bool:
    action = change.get("Action")
    if action == "Remove":
        return True
    return action == "Modify" and change.get("Replacement") in REPLACEMENT_RISKY


def console_url(stack_id: str | None, change_set_id: str | None) -> str | None:
    """Deep link to the change set in the AWS Console."""
    if not stack_id or not change_set_id:
        return None
    parts = change_set_id.split(":")
    if len(parts) < 6 or not parts[3]:
        return None
    region = parts[3]
    return (
        f"https://{region}.console.aws.amazon.com/cloudformation/home?region={region}"
        f"#/stacks/changesets/changes?stackId={quote(stack_id, safe='')}"
        f"&changeSetId={quote(change_set_id, safe='')}"
    )


def analyze(changeset: dict, service: str, ignored_types: list[str]) -> dict:
    """Reduce a describe-change-set response to what a reviewer needs."""
    status = changeset.get("Status", "UNKNOWN")
    reason = changeset.get("StatusReason") or ""
    changes = [
        item["ResourceChange"]
        for item in changeset.get("Changes") or []
        if item.get("Type", "Resource") == "Resource" and "ResourceChange" in item
    ]

    if status == "CREATE_COMPLETE":
        outcome = "changes"
    elif status == "FAILED" and any(marker in reason.lower() for marker in NO_CHANGE_MARKERS):
        outcome = "no_changes"
        changes = []
    else:
        outcome = "failed"

    counts = {"Add": 0, "Modify": 0, "Remove": 0, "Other": 0}
    rows, destructive, ignored = [], [], []
    for change in changes:
        action = change.get("Action", "Unknown")
        counts[action if action in counts else "Other"] += 1
        row = {
            "action": action,
            "replacement": change.get("Replacement") or "-",
            "resource_type": change.get("ResourceType", "Unknown"),
            "logical_id": change.get("LogicalResourceId", "Unknown"),
            "physical_id": change.get("PhysicalResourceId") or "",
        }
        row["destructive"] = False
        if is_destructive(change):
            if row["resource_type"] in ignored_types:
                ignored.append(row)
            else:
                row["destructive"] = True
                destructive.append(row)
        rows.append(row)

    return {
        "service": service,
        "stack_name": changeset.get("StackName"),
        "stack_id": changeset.get("StackId"),
        "change_set_name": changeset.get("ChangeSetName"),
        "change_set_id": changeset.get("ChangeSetId"),
        "console_url": console_url(changeset.get("StackId"), changeset.get("ChangeSetId")),
        "aws_status": status,
        "aws_status_reason": reason,
        "outcome": outcome,
        "counts": counts,
        "total_changes": len(rows),
        "destructive": destructive,
        "ignored_destructive": ignored,
        "changes": rows,
    }


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _describe(row: dict) -> str:
    what = "Remove" if row["action"] == "Remove" else f"Replace ({row['replacement']})"
    return f"`{row['logical_id']}` ({row['resource_type']}): {what}"


def render_summary(result: dict) -> str:
    """Markdown for one service, written to the GitHub job summary."""
    lines = [f"### `{result['service']}` → stack `{result['stack_name']}`", ""]

    if result["outcome"] == "no_changes":
        lines += ["No changes. There is nothing to review or execute for this service.", ""]
        return "\n".join(lines)

    if result["outcome"] == "failed":
        lines += [
            "> [!WARNING]",
            f"> The change set could not be created (status `{result['aws_status']}`).",
            f"> Reason from CloudFormation: {result['aws_status_reason'] or 'none given'}",
            "",
        ]
        return "\n".join(lines)

    destructive = result["destructive"]
    if destructive:
        lines += [
            "> [!CAUTION]",
            f"> **{len(destructive)} resource(s) will be DELETED or REPLACED if this change set is executed.**",
            "> A replacement deletes the resource and creates a new, empty one. Check for data loss before executing.",
        ]
        lines += [f"> - {_describe(row)}" for row in destructive]
        lines.append("")

    link = f"[Open in the AWS Console]({result['console_url']})" if result["console_url"] else "(no console link)"
    counts = result["counts"]
    count_text = f"Add {counts['Add']}, Modify {counts['Modify']}, Remove {counts['Remove']}"
    if counts["Other"]:
        count_text += f", Other {counts['Other']}"
    lines += [
        f"**Change set:** `{result['change_set_name']}`  {link}",
        "",
        f"**Changes:** {count_text}",
        "",
        "| Action | Replacement | Resource type | Logical ID |",
        "|---|---|---|---|",
    ]
    ordered = sorted(result["changes"], key=lambda row: not row["destructive"])
    for row in ordered:
        action = f"**{row['action']}**" if row["destructive"] else row["action"]
        lines.append(
            f"| {_cell(action)} | {_cell(row['replacement'])} | {_cell(row['resource_type'])} | `{_cell(row['logical_id'])}` |"
        )
    lines.append("")

    if result["ignored_destructive"]:
        types = sorted({row["resource_type"] for row in result["ignored_destructive"]})
        lines += [
            f"Not flagged (known noise, see `ignored_resource_types` in deploy-config.json): {', '.join(types)}",
            "",
        ]
    lines += ["Nothing has been executed. Review the change set, then execute it from the AWS Console.", ""]
    return "\n".join(lines)


def render_overview(order: list[str], results: dict[str, dict]) -> str:
    """One table for the whole run, in the order the change sets should be executed."""
    lines = ["## Change sets created by this run", ""]
    flagged = [results[name] for name in order if name in results and results[name]["destructive"]]
    if flagged:
        total = sum(len(result["destructive"]) for result in flagged)
        lines += [
            "> [!CAUTION]",
            f"> **{total} resource(s) across {len(flagged)} service(s) will be DELETED or REPLACED.**",
        ]
        for result in flagged:
            lines += [f"> - `{result['service']}`: {_describe(row)}" for row in result["destructive"]]
        lines.append("")

    lines += [
        "| # | Service | Stack | Change set | Add | Modify | Remove | Deletes or replacements |",
        "|---|---|---|---|---|---|---|---|",
    ]
    step = 0
    pending = 0
    for name in order:
        result = results.get(name)
        if result is None:
            pending += 1
            lines.append(f"| | `{name}` | | Not created (job failed or did not run) | | | | |")
        elif result["outcome"] == "no_changes":
            lines.append(f"| | `{name}` | `{result['stack_name']}` | No changes | 0 | 0 | 0 | |")
        elif result["outcome"] == "failed":
            pending += 1
            lines.append(
                f"| | `{name}` | `{result['stack_name']}` | Failed: {_cell(result['aws_status_reason'])} | | | | |"
            )
        else:
            step += 1
            counts = result["counts"]
            name_cell = f"`{result['change_set_name']}`"
            if result["console_url"]:
                name_cell = f"[{name_cell}]({result['console_url']})"
            warn = f"**{len(result['destructive'])}**" if result["destructive"] else "0"
            lines.append(
                f"| {step} | `{name}` | `{result['stack_name']}` | {name_cell} | "
                f"{counts['Add']} | {counts['Modify']} | {counts['Remove']} | {warn} |"
            )
    lines.append("")

    if step:
        lines += [
            "**Nothing has been executed.** To deploy, open each link, review the changes and choose "
            "*Execute change set*. Follow the numbered order, because later services depend on earlier ones.",
            "",
        ]
    else:
        lines += ["No change sets need to be executed.", ""]
    if pending:
        lines += [f"{pending} service(s) have no change set. See the failed job for details.", ""]
    return "\n".join(lines)


def _write(path: str | None, text: str, append: bool = False) -> None:
    if not path or path == "-":
        sys.stdout.write(text)
        return
    with open(path, "a" if append else "w", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    one = commands.add_parser("analyze", help="analyze one describe-change-set JSON file")
    one.add_argument("--changeset", required=True, help="describe-change-set JSON file")
    one.add_argument("--service", required=True, help="service folder name")
    one.add_argument("--config", default=DEFAULT_CONFIG)
    one.add_argument("--summary-out", help="append Markdown here (default: stdout)")
    one.add_argument("--result-out", help="write the result JSON here")

    many = commands.add_parser("overview", help="summarize all results of a run")
    many.add_argument("--results-dir", required=True, help="folder with <service>.result.json files")
    many.add_argument("--order", required=True, help="JSON list of services in execution order")
    many.add_argument("--summary-out", help="append Markdown here (default: stdout)")

    args = parser.parse_args(argv)

    if args.command == "analyze":
        with open(args.changeset, encoding="utf-8") as handle:
            changeset = json.load(handle)
        result = analyze(changeset, args.service, load_ignored_types(args.config))
        if args.result_out:
            _write(args.result_out, json.dumps(result, indent=2) + "\n")
        _write(args.summary_out, render_summary(result) + "\n", append=True)
        if result["outcome"] == "failed":
            print(
                f"error: change set {result['change_set_name']} is {result['aws_status']}: "
                f"{result['aws_status_reason']}",
                file=sys.stderr,
            )
            return 1
        return 0

    order = json.loads(args.order)
    results = {}
    for name in order:
        path = os.path.join(args.results_dir, f"{name}.result.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                results[name] = json.load(handle)
    _write(args.summary_out, render_overview(order, results) + "\n", append=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
