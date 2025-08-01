from .artifact_schema import artifact_schema

save_artifact_schema = {
    "type": "object",
    "properties": {"artifact": artifact_schema},
    "required": ["artifact"],
}
