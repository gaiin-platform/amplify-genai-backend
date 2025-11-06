import json
import os
import jose
from pycommon import required_env_vars
from pycommon.exceptions import ClaimException
from pycommon.dal import DAL, Backend, UserABC
from pycommon.dal.errors import NotFound
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from jose import jwt
import pycommon.dal.providers.aws

dal = DAL(Backend.AWS)

from pycommon.logger import getLogger
logger = getLogger("object_access_users")


def _get_user_identifier(token_payload: dict) -> tuple[str, str]:
    """Get user identifier and type from token payload.
    Returns: (identifier_value, identifier_type)
    """
    # Try identifiers in priority order
    immutable_id = token_payload.get("immutable_id")
    if immutable_id and immutable_id.strip():
        return immutable_id, "immutable_id"
    
    # Check username field (Cognito's actual username, often email)
    username = token_payload.get("username")
    if username and username.strip():
        return username, "username"
    
    # Fall back to email field if present
    email = token_payload.get("email")
    if email and email.strip():
        return email, "email"
    
    raise ValueError("No valid user identifier found in token")


def _find_user_by_property(properties: dict) -> tuple[UserABC, str] | None:
    """Helper to find a user by one of several properties.
    properties: dict of {property_name: property_value}
    returns: (user, property_name) or None if not found"""
    for k, v in properties.items():
        if not v:
            continue
        try:
            user: UserABC = dal.User.get_by_user_id(user_id=v)
            return user, k
        except NotFound:
            continue
    return None, ""


def _create_user(token_payload: dict) -> UserABC:
    """Helper to create a user from token payload."""
    user_id, id_type = _get_user_identifier(token_payload)
    
    # Get groups from either saml_groups or vu_groups (deployment-agnostic)
    groups_value = (
        token_payload.get("custom:saml_groups", "") or 
        token_payload.get("custom:vu_groups", "")
    )
    
    user: UserABC = dal.User(
        user_id=user_id,
        email=token_payload.get("email"),
        family_name=token_payload.get("family_name"),
        given_name=token_payload.get("given_name"),
        cust_saml_groups=groups_value,
    )
    user.save()
    return user


def _update_attributes(token_payload: dict, user: UserABC) -> UserABC:
    """Helper to update user attributes from token payload."""
    if token_payload.get("email"):
        user.email = token_payload.get("email")
    if token_payload.get("family_name"):
        user.family_name = token_payload.get("family_name")
    if token_payload.get("given_name"):
        user.given_name = token_payload.get("given_name")
    
    # Update groups from either saml_groups or vu_groups (deployment-agnostic)
    groups_value = (
        token_payload.get("custom:saml_groups", "") or 
        token_payload.get("custom:vu_groups", "")
    )
    if groups_value:
        user.cust_saml_groups = groups_value
    
    user.save()
    return user


def _update_admin_groups(user: UserABC) -> None:
    """
    Helper to update the admin config for AMPLIFY_GROUPS.
    This ensures that the user is added to any new groups and removed from any
    groups of which they are no longer a member.
    """
    config_id = "amplifyGroups"
    conf = dal.AdminConfig.get_config(config_id)

    if not conf:
        logger.info(f"No admin config found for {config_id}, skipping group sync")
        return
    saml_groups = json.loads(user.cust_saml_groups) if user.cust_saml_groups else []
    user_id = user.user_id
    # handle any case where we have a group in saml but it
    # is not in the config, or vice versa
    for g in saml_groups:
        if g not in conf:
            logger.info(f"adding new group {g} to config")
            conf[g] = {"members": [user_id], "createdBy": "JIT_PROVISIONER"}
    for g in conf:
        ignore_groups: str = conf[g].get("createdBy", None)
        if ignore_groups and ignore_groups != "JIT_PROVISIONER" and ignore_groups != "Cognito_Sync":
            logger.info(f"ignoring group {g} because createdBy is {ignore_groups}")
            continue
        logger.debug(f"looking for group {g}")
        if g in saml_groups and user_id not in conf.get(g, {}).get("members", []):
            logger.info(f"adding user {user_id} to group {g}")
            conf[g].setdefault("members", []).append(user_id)
        elif g not in saml_groups and user_id in conf.get(g, {}).get("members", []):
            logger.info(f"removing user {user_id} from group {g}")
            if conf[g].get("members"):
                conf[g]["members"].remove(user_id)
    dal.AdminConfig.set_config(config_id, conf)


@required_env_vars({
    "ACCOUNTS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM],
})
def create_or_update_user(event, context):
    """Lambda function to create or update a user based on Cognito data.

    This function requires the access token from Cognito to extract user information.
    It checks if the user exists in the database and updates or creates the user accordingly.

    The access token MUST have the following attributes embedded:
    - custom:immutable_id
    - email

    The immutable_id is what is the _username_ henceforth the user_id in our system.
    The other data passed it will be automatically updated if it has changed. For
    example: if immutable_id "smithj" has email "smithj@example.com", but then later
    the email changes to "jonesj@example.com", this function will update it. However the
    immutable_id can never change.

    The general logic to this function looks like this:
    1. validate access token, return 401 if invalid
    2. extract user info from token
    3. check if user exists in database
    3a. if yes: update attributes as needed
    3b. if no: check if the email exists and the record version needs updated
    3c1. If the email exists then we need to update the attributes and return a
         message stating that the user needs to be upgraded.
    3c2. If the email does not exist then we create a new user.
    """  # noqa: E501


    # snag our auth header - because this is pre-auth, we cannot use the @validated
    # decorator.
    header = event.get("headers", {}).get("Authorization", None)
    if not header:
        return {"statusCode": 401, "body": "Unauthorized: No access token provided"}

    # make sure our access token bearer token is available
    auth_header = header.split()
    if len(auth_header) != 2 or auth_header[0].lower() != "bearer":
        return {"statusCode": 401, "body": "Unauthorized: Invalid token format"}

    try:
        # snag key variables
        base_url = os.environ["OAUTH_ISSUER_BASE_URL"]
        oauth_audience = os.environ["OAUTH_AUDIENCE"]
        token = auth_header[1]

        # verify the token structure...
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            return {"statusCode": 401, "body": "Unauthorized: No kid in token header"}

        # get the JWKS to validate the signature
        jwks = pycommon.authz.get_jwks_for_url(base_url)

        # try to find a matching key id (kid)
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not key:
            return {"statusCode": 401, "body": "Unauthorized: No matching key found"}

        # validate the token
        payload = jwt.decode(
            token,
            key,
            algorithms=pycommon.authz.ALGORITHMS,
            audience=oauth_audience,
            issuer=base_url,
        )

        # get the payload to search in the DAL
        # support both immutable_id and email-based systems
        user_id, id_type = _get_user_identifier(payload)
        
        # Try to find existing user by the identified user_id
        user, prop_name = _find_user_by_property({id_type: user_id})
        
        # If not found by primary identifier, try other common fields for backwards compatibility
        if not user:
            # Try email if we used username or immutable_id
            if id_type in ["username", "immutable_id"] and payload.get("email"):
                user, prop_name = _find_user_by_property({"email": payload.get("email")})
            # Try username if we used email or immutable_id  
            if not user and id_type in ["email", "immutable_id"] and payload.get("username"):
                user, prop_name = _find_user_by_property({"username": payload.get("username")})
        # A user has not been found; need to create one
        if not user:
            _create_user(payload)
            return {"statusCode": 201, "body": json.dumps({"message": "User created"})}

        # we have a user...
        # this next part will be fleshed out & differentiated when the
        # upgrade logic is in place
        if prop_name == "email":
            # user needs to have attributes updated, potentially and if
            # the version is old enough, upgraded.
            _update_attributes(payload, user)
            _update_admin_groups(user)
        if prop_name == "immutable_id":
            # user found by immutable_id, so just update attributes as needed
            _update_attributes(payload, user)
            _update_admin_groups(user)

        return {"statusCode": 200, "body": json.dumps({"message": "User updated"})}

    except (
        ClaimException,
        jose.JWTError,
        jose.ExpiredSignatureError,
        jose.exceptions.JWTClaimsError,
    ) as e:
        logger.error(f"Token error: {str(e)}")
        return {"statusCode": 401, "body": json.dumps({"message": f"Unauthorized: {str(e)}"})}

