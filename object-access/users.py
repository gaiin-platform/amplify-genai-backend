import os
import jose
from pycommon import required_env_vars
from pycommon.exceptions import ClaimException
from pycommon.dal import DAL, Backend, UserABC
from pycommon.dal.errors import NotFound
from jose import jwt
import pycommon.dal.providers.aws

dal = DAL(Backend.AWS)


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


@required_env_vars("OAUTH_ISSUER_BASE_URL", "OAUTH_AUDIENCE", "ACCOUNTS_DYNAMO_TABLE")
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

    print("Creating or updating user")

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
        print(f"token header: {header}, kid: {kid}")
        if not kid:
            return {"statusCode": 401, "body": "Unauthorized: No kid in token header"}

        # get the JWKS to validate the signature
        jwks = pycommon.authz.get_jwks_for_url(base_url)
        print(f"jwks: {jwks}")

        # try to find a matching key id (kid)
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        print(f"found key: {key}")
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
        # while immutable_id is the primary key, we also want to check email due to
        # previous system versions that did not enforce immutable_id as primary key.
        user, prop_name = _find_user_by_property(
            {"immutable_id": payload.get("immutable_id"), "email": payload.get("email")}
        )
        # A user has not been found; need to create one
        if not user:
            # create user (NOTE: we _always_ use the ABC classes in business logic code)
            # so that the implementation can be hot-swapped if needed.
            user: UserABC = dal.User(
                user_id=payload.get("immutable_id"),
                email=payload.get("email"),
                family_name=payload.get("family_name"),
                given_name=payload.get("given_name"),
                cust_vu_groups=payload.get("custom:vu_groups", ""),
                cust_vu_saml_groups=payload.get("custom:vu_saml_groups", ""),
            )
            user.save()
            return {"statusCode": 201, "body": "User created successfully"}

        # we have a user...
        if prop_name == "email":
            # user needs to have attributes updated, potentially and if
            # the version is old enough, upgraded.
            print(f"found user user by email. details: {user}")
            if payload.get("email"):
                user.email = payload.get("email")
            if payload.get("family_name"):
                user.family_name = payload.get("family_name")
            if payload.get("given_name"):
                user.given_name = payload.get("given_name")
            if payload.get("custom:vu_groups"):
                user.cust_vu_groups = payload.get("custom:vu_groups")
            if payload.get("custom:vu_saml_groups"):
                user.cust_saml_groups = payload.get("custom:vu_saml_groups")
            user.save()
        if prop_name == "immutable_id":
            # user found by immutable_id, so just update attributes as needed
            print(f"found user by immutable_id. details: {user}")

        return {"statusCode": 200, "body": "User creation or update logic goes here"}

    except (
        ClaimException,
        jose.JWTError,
        jose.ExpiredSignatureError,
        jose.exceptions.JWTClaimsError,
    ) as e:
        print(f"Token error: {str(e)}")
        return {"statusCode": 401, "body": f"Unauthorized: {str(e)}"}


def upgrade_user(event, context):
    print("Upgrading user")
    print(event)
    print(context)
    pass
