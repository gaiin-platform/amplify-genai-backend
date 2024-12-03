
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from common.ops import op
from common.validate import validated
from decimal import Decimal
import boto3
import os
import uuid


def get_accounts_for_user(user):
    dynamodb = boto3.resource('dynamodb')
    accounting_table_name = os.environ['ACCOUNTS_DYNAMO_TABLE']
    users_table = dynamodb.Table(accounting_table_name)

    try:
        # Attempt to get the item for the specified user from the DynamoDB table.
        response = users_table.get_item(
            Key={
                'user': user
            }
        )
        # Check if 'Item' exists in the response and has an 'accounts' attribute.
        if 'Item' in response and 'accounts' in response['Item']:
            print(f"Accounts found for user {user}")
            # Return the list of accounts.
            return response['Item']['accounts']
        else:
            # Return an empty list if 'accounts' is not found.
            print(f"No accounts found for user {user}")
            return []
    except Exception as e:
        # Handle potential errors and return an empty list.
        print(f"An error occurred while retrieving accounts for user {user}: {e}")
        return []


def save_accounts_for_user(user, accounts_list):
    dynamodb = boto3.resource('dynamodb')
    accounting_table_name = os.environ['ACCOUNTS_DYNAMO_TABLE']
    users_table = dynamodb.Table(accounting_table_name)

    #clean up rateLimit in accounts:
    for account in accounts_list:
        rateLimit = account['rateLimit']
        if  rateLimit.get("rate", None):
            account['rateLimit']["rate"] = Decimal(str(rateLimit["rate"]))

    try:
        # Put (or update) the item for the specified user in the DynamoDB table
        response = users_table.put_item(
            Item={
                'user': user,
                'accounts': accounts_list
            }
        )

        # Check if the response was successful
        if response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200:
            print(f"Accounts for user {user} saved successfully")
            return {
                'success': True,
                'message': 'Accounts saved successfully'
            }
        else:
            print(f"Failed to save accounts for user {user}")
            return {
                'success': False,
                'message': 'Failed to save accounts'
            }
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred while saving accounts for user {user}: {e}")
        return {
            'success': False,
            'message': 'An error occurred while saving accounts'
        }

def create_charge(account_id, charge, description, user, details):
    dynamodb = boto3.resource('dynamodb')
    request_id = str(uuid.uuid4())
    charge_as_decimal = Decimal(str(charge))
    accounting_table_name = os.environ['ACCOUNTING_DYNAMO_TABLE']
    requests_table = dynamodb.Table(accounting_table_name)

    print(f"User {user} is creating a new charge request with id {request_id} for {charge_as_decimal} in {accounting_table_name}")


    response = requests_table.put_item(
        Item={
            'id': request_id,
            'account_id': account_id,
            'charge': charge_as_decimal,
            'description': description,
            'user': user,
            'details': details
        }
    )

    if response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200:
        print(f"Charge request {request_id} stored successfully")
        return {
            'success': True,
            'message': 'Charge request stored successfully',
            'request_id': request_id
        }
    else:
        print(f"Failed to create charge request {request_id}")
        return {
            'success': False,
            'message': 'Failed to store charge request'
        }


@validated("create_charge")
def charge_request(event, context, user, name, data):
    account_id = data['data']['accountId']
    charge = data['data']['charge']
    description = data['data']['description']
    details = data['data']['details']

    # Call the core business logic function within the request handling function
    return create_charge(account_id, charge, description, user, details)


@op(
    path="/state/accounts/get",
    name="getUserAccounts",
    method="GET",
    tags=["accounts"],
    description="Get a list of the user's accounts that costs are charged to.",
    params={
    }
)
@validated("get")
def get_accounts(event, context, user, name, data):
    # accounts/get
    accounts = get_accounts_for_user(user)

    return {'success': True, 'message': 'Successfully fetched accounts', 'data': accounts}


@validated("save")
def save_accounts(event, context, user, name, data):
    # accounts/get
    data = data['data']
    return save_accounts_for_user(user, data['accounts'])
