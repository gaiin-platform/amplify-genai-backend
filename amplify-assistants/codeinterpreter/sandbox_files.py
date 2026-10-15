# Copyright (c) 2024 Vanderbilt University
"""Helpers for detecting sandbox output files. No AWS imports so they can be unit tested."""

_SIGNATURE_KEYS = ("size", "fileSize", "lastModified", "modified", "mtime", "modifiedTime", "updatedAt")


def file_signature(block, resource):
    """Size / modified-time details from a listFiles entry, or None when it carries none.

    Lets a file that was rewritten at the same path (e.g. a retry overwriting deck.pptx)
    be told apart from one that was already there before the run.
    """
    sig = {}
    for source in (block, resource, block.get("annotations") or {}, block.get("_meta") or {}):
        if not isinstance(source, dict):
            continue
        for key in _SIGNATURE_KEYS:
            if source.get(key) is not None:
                sig[key] = source[key]
    return tuple(sorted((k, str(v)) for k, v in sig.items())) or None


def changed_sandbox_paths(files_before, files_after):
    """Paths that are new, or whose size/modified time changed, between two listings."""
    return sorted(
        p for p, sig in files_after.items()
        if p not in files_before or (sig is not None and files_before[p] is not None and sig != files_before[p])
    )
