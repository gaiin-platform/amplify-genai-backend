"""Extract a human-readable failure reason from an AgentCore executeCode result.

AgentCore reports a Python exception in structuredContent.stderr, but some failures
come back with isError=True and NO structuredContent at all; the reason is then only
in the text content blocks. Reading stderr alone produced an empty message
("Code execution error: "), which hid the cause from both the user and the model.
"""

NO_DETAIL_MESSAGE = "The sandbox reported an error but returned no details."


def extract_error_detail(result):
    structured = (result or {}).get("structuredContent") or {}
    stderr = structured.get("stderr") if isinstance(structured, dict) else ""
    if isinstance(stderr, str) and stderr.strip():
        return stderr.strip()

    texts = [
        block.get("text", "")
        for block in (result or {}).get("content", []) or []
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    block_text = "\n".join(t for t in texts if t).strip()
    if block_text:
        return block_text

    stdout = structured.get("stdout") if isinstance(structured, dict) else ""
    if isinstance(stdout, str) and stdout.strip():
        return stdout.strip()[-2000:]
    return NO_DETAIL_MESSAGE
