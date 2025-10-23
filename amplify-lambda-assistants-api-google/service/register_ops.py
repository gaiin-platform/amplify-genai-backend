
from pycommon.api.tools_ops import api_tools_register_handler

from pycommon.logger import getLogger
logger = getLogger("google_register_ops")

def integration_config_trigger(event, context):
    """
    Triggered by a DynamoDB stream event on the admin configs table.
    For the 'integrations' record, on MODIFY events: if the specified provider key
    (PROVIDER) is newly added to the data field, call register_ops.
    """
    logger.info("Admin Config Trigger invoked")
    PROVIDER = "google"
    for record in event.get("Records", []):
        if record.get("eventName") != "MODIFY":
            continue

        new_image = record.get("dynamodb", {}).get("NewImage", {})
        config_id = new_image.get("config_id", {}).get("S")
        if config_id != "integrations":
            continue

        old_image = record.get("dynamodb", {}).get("OldImage", {})
        new_data = new_image.get("data", {})
        old_data = old_image.get("data", {})

        def extract_keys(data_field):
            # Assuming data_field is stored as a DynamoDB Map attribute.
            if "M" in data_field:
                return set(data_field["M"].keys())
            # Assume it's already a native dict.
            return set(data_field.keys())

        old_keys = extract_keys(old_data)
        new_keys = extract_keys(new_data)
        logger.debug("Old keys: %s", old_keys)
        logger.debug("New keys: %s", new_keys)

        # If the provider key was absent before and is now present, register ops.
        if PROVIDER not in old_keys and PROVIDER in new_keys:
            logger.info("Registering %s ops (provider key added)", PROVIDER)
            result = register_ops()
            if not result.get("success"):
                logger.error("Failed to register %s ops", PROVIDER)
        else:
            logger.debug("Provider %s ops already exists, skipping op registration", PROVIDER)


def register_ops() -> dict:
    """
    Registers operations by scanning the service folder for operations decorated 
    with @api_tool using the pycommon api_tools_register_handler.

    Returns:
        dict: A dictionary indicating the success or failure of the registration and a message.
    """
    
    try:
        # Configure to scan the service directory
        include_dirs = ["service"]
        command = "register"
        
        result = api_tools_register_handler(
            include_dirs=include_dirs,
            command=command,
            data={},
            current_user="system"
        )
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to register operations: {str(e)}",
            "operations_count": 0
        }