"""Validation for deployment system prompts before admin-config persistence."""

MAX_SYSTEM_PROMPT_BYTES = 16 * 1024


def validate_system_prompts(data):
    """Return a user-facing validation error, or None when all prompt texts fit."""
    prompts = data.get("prompts") if isinstance(data, dict) else None
    if not isinstance(prompts, dict):
        return "System prompts must include a prompts object."

    for key, record in prompts.items():
        text = record.get("text") if isinstance(record, dict) else None
        if not isinstance(text, str):
            return f'System prompt "{key}" must contain text.'
        try:
            text.encode("utf-16-le")
        except UnicodeEncodeError:
            return f'System prompt "{key}" contains an unpaired surrogate and cannot be saved.'
        try:
            byte_count = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            return f'System prompt "{key}" contains invalid Unicode and cannot be saved.'
        if byte_count > MAX_SYSTEM_PROMPT_BYTES:
            return (
                f'System prompt "{key}" is {byte_count:,} UTF-8 bytes; '
                f'the maximum is {MAX_SYSTEM_PROMPT_BYTES:,} bytes.'
            )
    return None


def update_system_prompts(data, persist):
    """Reject invalid prompt payloads before invoking the persistence callback."""
    error = validate_system_prompts(data)
    if error:
        return {"success": False, "message": error}
    return persist(data)
