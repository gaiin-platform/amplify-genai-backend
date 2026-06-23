"""
Amazon Bedrock AgentCore Web Search Provisioner

CloudFormation custom-resource Lambda that provisions (and tears down) the
Amazon Bedrock AgentCore Gateway + Web Search tool target used by the
`bedrock_agentcore` web search provider. This automates the otherwise-manual
console setup so it ships with the rest of the services.

What it does on Create/Update (when WEB_SEARCH_AGENTCORE_ENABLED=true):
  1. Ensures an MCP Gateway exists (idempotent by name) whose inbound authorizer
     trusts the app's existing Cognito user pool/app client. This lets the chat
     backend authorize gateway calls with the caller's own access token
     ("user_token" auth mode) - no extra secret to provision.
  2. Ensures a Web Search tool target (connectorId "web-search") exists on the
     gateway, authorized via the gateway's IAM service role.
  3. Publishes the resulting gateway URL / region / auth mode into the admin
     `webSearchConfig` item so the runtime can find it. It only flips the active
     provider to `bedrock_agentcore` when auto-enable is on AND no other provider
     is already configured (never clobbers an admin's existing choice).

Design notes:
  - Web Search connector is only available in us-east-1 today.
  - This is intentionally FAIL-SAFE: any error (including the running boto3 not
    yet knowing the bedrock-agentcore-control API) is logged and reported to
    CloudFormation as SUCCESS so a brand-new capability never blocks a deploy.
    Provisioning status is included in the response Data and the logs.
"""

import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AgentCore control-plane service name (used to create the boto3 client).
AGENTCORE_CONTROL_SERVICE = "bedrock-agentcore-control"
# The built-in web search connector id and tool configuration name.
WEB_SEARCH_CONNECTOR_ID = "web-search"
WEB_SEARCH_TOOL_CONFIG_NAME = "WebSearch"
# Admin config item that the runtime reads.
WEB_SEARCH_CONFIG_ID = "webSearchConfig"


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _agentcore_region() -> str:
    # Web Search connector is us-east-1 only today; allow override but default safely.
    return os.environ.get("WEB_SEARCH_AGENTCORE_REGION", "us-east-1")


def _discovery_url() -> str:
    """Build the OIDC discovery URL for the existing Cognito user pool."""
    issuer = (os.environ.get("OAUTH_ISSUER_BASE_URL") or "").rstrip("/")
    if not issuer:
        return ""
    return f"{issuer}/.well-known/openid-configuration"


def _get_agentcore_client():
    """
    Create the AgentCore control-plane client. Raises if the running boto3
    version doesn't know the service yet (caught by the fail-safe handler).
    """
    return boto3.client(AGENTCORE_CONTROL_SERVICE, region_name=_agentcore_region())


# ---------------------------------------------------------------------------
# Gateway + target management (idempotent)
# ---------------------------------------------------------------------------

def _find_gateway_by_name(client, name: str):
    """Return the gateway summary dict whose name matches, or None."""
    paginator_kwargs = {}
    while True:
        response = client.list_gateways(**paginator_kwargs)
        for gw in response.get("items", response.get("gateways", [])):
            if gw.get("name") == name:
                return gw
        next_token = response.get("nextToken")
        if not next_token:
            return None
        paginator_kwargs = {"nextToken": next_token}


def _build_authorizer_configuration():
    """
    Inbound JWT authorizer pointing at the existing Cognito user pool, accepting
    the app's Cognito app client. Cognito access tokens carry `client_id`
    (not `aud`), so we match on allowedClients.
    """
    discovery_url = _discovery_url()
    client_id = os.environ.get("COGNITO_CLIENT_ID")
    config = {"customJWTAuthorizer": {"discoveryUrl": discovery_url}}
    if client_id:
        config["customJWTAuthorizer"]["allowedClients"] = [client_id]
    return config


def _ensure_gateway(client, name: str, role_arn: str) -> dict:
    """Create or update the MCP gateway. Returns the gateway detail dict."""
    authorizer_config = _build_authorizer_configuration()
    existing = _find_gateway_by_name(client, name)

    common = {
        "name": name,
        "roleArn": role_arn,
        "protocolType": "MCP",
        "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": authorizer_config,
        "description": "Amplify web search via Bedrock AgentCore (managed by deployment).",
    }

    if existing:
        gateway_id = existing.get("gatewayId") or existing.get("gatewayIdentifier")
        status = (existing.get("status") or "").upper()
        # The list summary may not carry status; fetch the detail to be sure.
        if not status and gateway_id:
            try:
                detail = client.get_gateway(gatewayIdentifier=gateway_id)
                status = (detail.get("status") or "").upper()
            except ClientError as e:
                logger.warning("Could not read gateway status for %s: %s", gateway_id, str(e))
        # A gateway stuck in FAILED can't be repaired with update_gateway (its
        # dependencies were never created), so delete and recreate it cleanly.
        if status == "FAILED":
            logger.info("Existing gateway %s is in FAILED state; deleting and recreating", name)
            _delete_gateway_and_targets(client, name)
            # delete_gateway is async - wait for it to disappear before recreating
            # with the same name to avoid a ConflictException.
            if not _wait_for_gateway_deleted(client, name):
                logger.warning("Timed out waiting for FAILED gateway %s to delete; attempting create anyway", name)
        else:
            logger.info("Updating existing AgentCore gateway: %s (%s)", name, gateway_id)
            return client.update_gateway(gatewayIdentifier=gateway_id, **common)

    logger.info("Creating AgentCore gateway: %s", name)
    return _create_gateway_with_retry(client, common)


def _gateway_identifier(gateway: dict) -> str:
    return gateway.get("gatewayId") or gateway.get("gatewayIdentifier")


def _wait_for_gateway_deleted(client, name: str, timeout_seconds: int = 180, poll_seconds: int = 5) -> bool:
    """
    Poll until no gateway with the given name exists. delete_gateway is
    asynchronous, so we must wait before recreating with the same name to avoid
    a ConflictException.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _find_gateway_by_name(client, name) is None:
            return True
        time.sleep(poll_seconds)
    return False


def _wait_for_gateway_ready(client, gateway_id: str, timeout_seconds: int = 240, poll_seconds: int = 5) -> bool:
    """
    Poll until the gateway reports READY. A freshly created (or just-updated)
    gateway is transiently CREATING/UPDATING, and target creation requires a
    READY gateway - so we must wait before creating the web search target.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            detail = client.get_gateway(gatewayIdentifier=gateway_id)
        except ClientError as e:
            logger.warning("get_gateway during readiness wait failed: %s", str(e))
            return False
        status = (detail.get("status") or "").upper()
        if status == "READY":
            return True
        if status == "FAILED":
            logger.warning("Gateway %s entered FAILED while waiting for READY", gateway_id)
            return False
        time.sleep(poll_seconds)
    logger.warning("Timed out waiting for gateway %s to become READY", gateway_id)
    return False


def _create_gateway_with_retry(client, common: dict, attempts: int = 6, delay_seconds: int = 10) -> dict:
    """
    Create the gateway, retrying on transient conflicts (e.g. the prior gateway
    of the same name is still finishing deletion).
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return client.create_gateway(**common)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ConflictException", "ThrottlingException", "ServiceQuotaExceededException") and attempt < attempts:
                logger.info("create_gateway attempt %d failed with %s; retrying in %ds", attempt, code, delay_seconds)
                last_error = e
                time.sleep(delay_seconds)
                continue
            raise
    if last_error:
        raise last_error


def _find_web_search_target(client, gateway_id: str):
    """Return the web search target dict on the gateway, or None."""
    kwargs = {"gatewayIdentifier": gateway_id}
    while True:
        response = client.list_gateway_targets(**kwargs)
        for target in response.get("items", response.get("targets", [])):
            if target.get("name") == "web-search-tool":
                return target
        next_token = response.get("nextToken")
        if not next_token:
            return None
        kwargs = {"gatewayIdentifier": gateway_id, "nextToken": next_token}


def _ensure_web_search_target(client, gateway_id: str) -> dict:
    """Create the web search tool target if it doesn't already exist."""
    target_configuration = {
        "mcp": {
            "connector": {
                "source": {"connectorId": WEB_SEARCH_CONNECTOR_ID},
                "configurations": [
                    {"name": WEB_SEARCH_TOOL_CONFIG_NAME, "parameterValues": {}}
                ],
            }
        }
    }
    credential_provider_configurations = [
        {"credentialProviderType": "GATEWAY_IAM_ROLE"}
    ]

    existing = _find_web_search_target(client, gateway_id)
    if existing:
        logger.info("Web search target already present on gateway %s", gateway_id)
        return existing

    logger.info("Creating web search target on gateway %s", gateway_id)
    last_error = None
    for attempt in range(1, 7):
        try:
            return client.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name="web-search-tool",
                targetConfiguration=target_configuration,
                credentialProviderConfigurations=credential_provider_configurations,
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            # The gateway can briefly reject target creation while it settles.
            if code in ("ConflictException", "ValidationException", "ThrottlingException") and attempt < 6:
                logger.info("create_gateway_target attempt %d failed with %s; retrying", attempt, code)
                last_error = e
                time.sleep(10)
                continue
            raise
    if last_error:
        raise last_error


def _delete_gateway_and_targets(client, name: str) -> None:
    """Best-effort teardown of the gateway and its targets."""
    existing = _find_gateway_by_name(client, name)
    if not existing:
        logger.info("No AgentCore gateway named %s to delete", name)
        return
    gateway_id = _gateway_identifier(existing)

    # Targets must be removed before the gateway.
    try:
        kwargs = {"gatewayIdentifier": gateway_id}
        while True:
            response = client.list_gateway_targets(**kwargs)
            targets = response.get("items", response.get("targets", []))
            for target in targets:
                target_id = target.get("targetId") or target.get("targetIdentifier")
                if target_id:
                    client.delete_gateway_target(
                        gatewayIdentifier=gateway_id, targetId=target_id
                    )
                    logger.info("Deleted gateway target %s", target_id)
            next_token = response.get("nextToken")
            if not next_token:
                break
            kwargs = {"gatewayIdentifier": gateway_id, "nextToken": next_token}
    except ClientError as e:
        logger.warning("Error deleting gateway targets: %s", str(e))

    client.delete_gateway(gatewayIdentifier=gateway_id)
    logger.info("Deleted AgentCore gateway %s", gateway_id)


# ---------------------------------------------------------------------------
# Admin config wiring (so the runtime can find the gateway)
# ---------------------------------------------------------------------------

def _publish_admin_config(gateway_url: str) -> None:
    """
    Merge the gateway URL / region / auth mode into the admin webSearchConfig
    item. Only sets bedrock_agentcore as the active provider when auto-enable is
    on AND no other provider is currently configured.
    """
    table_name = os.environ.get("AMPLIFY_ADMIN_DYNAMODB_TABLE")
    if not table_name:
        logger.warning("AMPLIFY_ADMIN_DYNAMODB_TABLE not set; skipping admin config update")
        return

    table = boto3.resource("dynamodb").Table(table_name)
    region = _agentcore_region()
    auto_enable = _bool_env("WEB_SEARCH_AGENTCORE_AUTOENABLE", False)

    response = table.get_item(Key={"config_id": WEB_SEARCH_CONFIG_ID})
    existing_data = (response.get("Item") or {}).get("data", {}) or {}
    current_provider = existing_data.get("provider")

    data = dict(existing_data)
    data["bedrockAgentCoreGatewayUrl"] = gateway_url
    data["bedrockAgentCoreRegion"] = region
    data["bedrockAgentCoreAuthMode"] = "user_token"
    data["allowUserWebSearchKeys"] = existing_data.get("allowUserWebSearchKeys", False)

    # Only take over the active provider if asked to AND nothing else is set up.
    if auto_enable and (not current_provider or current_provider == "bedrock_agentcore"):
        data["provider"] = "bedrock_agentcore"
        data["isEnabled"] = True
        logger.info("Auto-enabling bedrock_agentcore as the active web search provider")
    else:
        logger.info(
            "Storing AgentCore gateway config without changing active provider (current=%s, autoEnable=%s)",
            current_provider, auto_enable,
        )

    table.put_item(Item={"config_id": WEB_SEARCH_CONFIG_ID, "data": data})


def _provision() -> dict:
    """Run the full provisioning flow and return a status dict."""
    role_arn = os.environ.get("AGENTCORE_GATEWAY_ROLE_ARN")
    gateway_name = os.environ.get("WEB_SEARCH_AGENTCORE_GATEWAY_NAME")

    if not role_arn:
        return {"status": "skipped", "reason": "AGENTCORE_GATEWAY_ROLE_ARN not set"}
    if not gateway_name:
        return {"status": "skipped", "reason": "WEB_SEARCH_AGENTCORE_GATEWAY_NAME not set"}
    if not _discovery_url():
        return {"status": "skipped", "reason": "OAUTH_ISSUER_BASE_URL not set"}

    client = _get_agentcore_client()

    gateway = _ensure_gateway(client, gateway_name, role_arn)
    gateway_id = _gateway_identifier(gateway)

    # Target creation requires a READY gateway, so wait for it to settle first.
    _wait_for_gateway_ready(client, gateway_id)

    # Re-read the gateway for a reliable URL (the create/update response may not
    # carry the final gatewayUrl).
    gateway_url = gateway.get("gatewayUrl") or gateway.get("gatewayEndpoint")
    try:
        detail = client.get_gateway(gatewayIdentifier=gateway_id)
        gateway_url = detail.get("gatewayUrl") or detail.get("gatewayEndpoint") or gateway_url
    except ClientError as e:
        logger.warning("Could not re-read gateway URL: %s", str(e))

    _ensure_web_search_target(client, gateway_id)

    if gateway_url:
        _publish_admin_config(gateway_url)

    return {
        "status": "provisioned",
        "gatewayId": gateway_id,
        "gatewayUrl": gateway_url,
        "region": _agentcore_region(),
    }


# ---------------------------------------------------------------------------
# CloudFormation custom-resource plumbing
# ---------------------------------------------------------------------------

def send_cfn_response(event, context, status, response_data):
    """Send a response back to CloudFormation (mirrors parameter_store_populator)."""
    import urllib3

    response_body = json.dumps({
        "Status": status,
        "Reason": f"See CloudWatch Log Stream: {context.log_stream_name}",
        "PhysicalResourceId": event.get("PhysicalResourceId") or context.log_stream_name,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": response_data,
    })

    logger.info("Sending CloudFormation response: %s", status)
    try:
        http = urllib3.PoolManager()
        result = http.request(
            "PUT",
            event["ResponseURL"],
            body=response_body,
            headers={"Content-Type": "application/json"},
        )
        logger.info("CloudFormation response sent: %s", result.status)
    except Exception as e:  # noqa: BLE001 - never raise from response sender
        logger.error("Error sending CloudFormation response: %s", e)

    return {"statusCode": 200}


def handle_cloudformation_request(event, context):
    request_type = event.get("RequestType")
    enabled = _bool_env("WEB_SEARCH_AGENTCORE_ENABLED", False)

    # Delete: best-effort teardown only when enabled; otherwise no-op.
    if request_type == "Delete":
        if enabled:
            try:
                gateway_name = os.environ.get("WEB_SEARCH_AGENTCORE_GATEWAY_NAME")
                if gateway_name:
                    _delete_gateway_and_targets(_get_agentcore_client(), gateway_name)
            except Exception as e:  # noqa: BLE001 - fail-safe on delete
                logger.warning("AgentCore teardown failed (continuing): %s", e)
        return send_cfn_response(event, context, "SUCCESS", {"Message": "Delete handled"})

    if not enabled:
        logger.info("WEB_SEARCH_AGENTCORE_ENABLED is false; skipping AgentCore provisioning")
        return send_cfn_response(event, context, "SUCCESS", {
            "Message": "AgentCore web search provisioning disabled"
        })

    # Create/Update: provision, but NEVER fail the deployment if the brand-new
    # API/permissions aren't available yet - report SUCCESS with status detail.
    try:
        result = _provision()
        logger.info("AgentCore provisioning result: %s", json.dumps(result, default=str))
        return send_cfn_response(event, context, "SUCCESS", {"Message": "OK", "Result": result})
    except Exception as e:  # noqa: BLE001 - fail-safe so a deploy is never blocked
        logger.error("AgentCore provisioning failed (non-blocking): %s", e, exc_info=True)
        return send_cfn_response(event, context, "SUCCESS", {
            "Message": "AgentCore provisioning skipped due to error (non-blocking)",
            "Error": str(e),
        })


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event, default=str))
    try:
        if "RequestType" in event:
            return handle_cloudformation_request(event, context)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Direct invocation not supported. Use CloudFormation custom resource."}),
        }
    except Exception as e:  # noqa: BLE001
        logger.error("Unexpected error: %s", e, exc_info=True)
        if "RequestType" in event:
            return send_cfn_response(event, context, "SUCCESS", {
                "Message": "Unexpected error (non-blocking)", "Error": str(e),
            })
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
