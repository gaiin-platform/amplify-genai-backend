# Copyright (c) 2024 Vanderbilt University
"""Resilient pandoc invocation for the document converter Lambda.

Kept free of AWS / pycommon imports so it can be unit tested locally.
"""

import os
import re
import subprocess
import time

PANDOC_BIN = os.environ.get("PANDOC_BIN", "/opt/bin/pandoc")
# pandoc normally finishes in about a second. These bounds keep every retry inside the
# Lambda timeout (180s) and let the client see the error marker before it stops polling.
PANDOC_TIMEOUT_SECONDS = 25
TOTAL_BUDGET_SECONDS = 70

# Markdown images: ![alt](target "optional title"). Models frequently emit
# placeholder or invented image URLs; pandoc aborts the whole conversion
# (exit 61) when it cannot fetch one, so on a retry they are replaced by their alt text.
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*[^)\s]+(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")


def strip_images(markdown_text: str) -> str:
    """Replace markdown images with their alt text (or nothing when empty)."""
    return _IMAGE_RE.sub(lambda m: m.group(1), markdown_text)


def _run(args, timeout):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _summarize(stderr_bytes: bytes, limit: int = 600) -> str:
    text = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
    return text[:limit] if text else "pandoc exited with an error"


def convert_with_fallbacks(input_path, output_path, template_path=None, logger=None):
    """Run pandoc, degrading gracefully instead of failing outright.

    Attempts, in order:
      1. as requested (template + original markdown)
      2. original markdown without images (template kept)
      3. original markdown without the template (images kept)
      4. no images and no template

    Returns (success, info). On success ``info`` lists what was dropped
    (e.g. ["images", "template"]); on failure it is the last pandoc error text.
    """
    with open(input_path, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    stripped_path = input_path + ".noimg.md"
    attempts = [("", input_path, template_path)]
    if _IMAGE_RE.search(original):
        with open(stripped_path, "w", encoding="utf-8") as f:
            f.write(strip_images(original))
        attempts.append(("images", stripped_path, template_path))
    if template_path:
        attempts.append(("template", input_path, None))
        if _IMAGE_RE.search(original):
            attempts.append(("images,template", stripped_path, None))

    last_error = "unknown error"
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    for dropped, source, template in attempts:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            last_error = f"{last_error} (stopped retrying after {TOTAL_BUDGET_SECONDS} seconds)"
            break
        args = [PANDOC_BIN]
        if template:
            args += ["--reference-doc", template]
        args += [source, "-o", output_path]
        try:
            result = _run(args, min(PANDOC_TIMEOUT_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            last_error = f"pandoc timed out after {int(min(PANDOC_TIMEOUT_SECONDS, remaining))} seconds"
            if logger:
                logger.error(last_error)
            continue
        except OSError as e:
            # pandoc binary missing / not executable: retrying cannot help
            return False, f"pandoc could not be started: {e}"

        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            dropped_list = [d for d in dropped.split(",") if d]
            if dropped_list and logger:
                logger.warning("Conversion succeeded only after dropping: %s", ", ".join(dropped_list))
            return True, dropped_list

        last_error = _summarize(result.stderr)
        if logger:
            logger.error(
                "pandoc attempt (dropped=%s) failed rc=%s: %s",
                dropped or "nothing", result.returncode, last_error,
            )

    return False, last_error
