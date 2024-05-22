import json
import os
import re
import boto3
from botocore.exceptions import ClientError
from common.validate import validated

@validated("read")
def get_emails(event, context, current_user, name, data, username): 
    query_params = event.get('queryStringParameters', {})
    print("Query params: ", query_params)
    email_prefix = query_params.get('emailprefix', '')
    if not email_prefix or not is_valid_email_prefix(email_prefix):
        return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid or missing email parameter'})
                }

    dynamodb = boto3.resource('dynamodb')
    cognito_user_table = dynamodb.Table(os.environ['COGNITO_USERS_TABLE'])

    try:
        print("Initiate query to cognito user dynamo table")
        response = cognito_user_table.scan(
                FilterExpression='begins_with(user_id, :email_prefix)',
                ExpressionAttributeValues={':email_prefix': email_prefix.lower()}
            )
        
        # print("Response: ", response)
        if 'Items' not in response:
            print("Failed to get matching emails")
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'Failed to get matching Emails'})
                }

        email_matches = [item['user_id'] for item in response['Items']]
        # print("Email matches:\n", email_matches)
        return {
                'statusCode': 200,
                'body': json.dumps({'emails' : email_matches})
                }

    except ClientError as e:
        print("Error: ", e.response['Error']['Message'])
        return {
                'statusCode': 500,
                'body': json.dumps({'error': str(e)})
                }
       
def is_valid_email_prefix(prefix):
    """ Validate the email prefix against a simple character check or regex. """
    if re.match(r"^[a-zA-Z0-9._%+@-]+$", prefix):
        return True
    return False



@validated("read")
def get_in_cognito_group(event, context, current_user, name, data, username): 
    dynamodb = boto3.resource('dynamodb')
    cognito_user_table = dynamodb.Table(os.environ['COGNITO_USERS_TABLE'])

    try:
        print("Initiate query to cognito user dynamo table for user: ", current_user)
        response = cognito_user_table.get_item(Key={'user_id': current_user})

        print("Response: ", response)

        if 'Item' not in response:
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'Failed to check cognito groups'})
                }

        cognito_groups = response['Item']['custom:vu_groups']
        print("cognito groups: ", cognito_groups)

        return {
                'statusCode': 200,
                'body': json.dumps({'cognitoGroups' : cognito_groups})
                }

    except ClientError as e:
        print("Error: ", e.response['Error']['Message'])
        return {
                'statusCode': 500,
                'body': json.dumps({'error': str(e)})
                }