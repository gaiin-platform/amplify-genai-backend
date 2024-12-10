
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import json
import os
import re
import boto3
from botocore.exceptions import ClientError
from common.validate import validated

@validated("read")
def get_emails(event, context, current_user, name, data): 
    query_params = event.get('queryStringParameters', {})
    print("Query params: ", query_params)
    email_prefix = query_params.get('emailprefix', '')
    if (not email_prefix or not is_valid_email_prefix(email_prefix)):
        return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid or missing email parameter'})
                }

    dynamodb = boto3.resource('dynamodb')
    cognito_user_table = dynamodb.Table(os.environ['COGNITO_USERS_TABLE'])

    try:
        print("Initiate query to cognito user dynamo table")
        response = None
        if email_prefix == '*':  # If the prefix is '*', get all entries
            response = cognito_user_table.scan(
                ProjectionExpression='user_id'
            )
        else:
            response = cognito_user_table.scan(
                    ProjectionExpression='user_id',
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
    if (prefix == '*'): return True
    """ Validate the email prefix against a simple character check or regex. """
    if re.match(r"^[a-zA-Z0-9._%+@-]+$", prefix):
        return True
    return False



@validated("read")
def get_user_groups(event, context, current_user, name, data): 
    resp_data = get_cognito_amplify_groups(current_user)
    return {
            'statusCode': resp_data["status"],
            'body': json.dumps(resp_data["data"])
            }


def get_cognito_amplify_groups(current_user): 
    dynamodb = boto3.resource('dynamodb')
    cognito_user_table = dynamodb.Table(os.environ['COGNITO_USERS_TABLE'])

    try:
        print("Initiate query to cognito user dynamo table for user: ", current_user)
        response = cognito_user_table.get_item(Key={'user_id': current_user})

        print("Response: ", response)

        if 'Item' not in response:
            return {
                'status': 404,
                'data': {'error': 'Failed to check cognito groups'}
                }

        cognito_groups = response['Item'].get('custom:vu_groups', [])
        amplify_groups = response['Item'].get('amplify_groups', [])

        print("cognito groups: ", cognito_groups)
        print("amplify groups", amplify_groups)

        return {
                'status': 200,
                'data': {'cognitoGroups' : cognito_groups,
                         'amplifyGroups' : amplify_groups}
                }

    except ClientError as e:
        print("Error: ", e.response['Error']['Message'])
        return {
                'status': 500,
                'data': {'error': str(e)}
                }
    

@validated("in_group")
def in_cognito_amplify_groups(event, context, current_user, name, data): 
    dynamodb = boto3.resource('dynamodb')
    cognito_user_table = dynamodb.Table(os.environ['COGNITO_USERS_TABLE'])
    data = data['data']

    cogn_groups = data.get('cognitoGroups',[])
    amp_groups = data.get('amplifyGroups',[])

    try:
        print("Initiate query to cognito user dynamo table for user: ", current_user)
        response = cognito_user_table.get_item(Key={'user_id': current_user})


        if 'Item' not in response:
            return {
                    'statusCode': 404,
                    'body': json.dumps({'success': False, 
                                        'error': 'Failed to check cognito groups'
                                        })
                    }


        # Convert stored group strings to lists
        users_cognito_groups = parse_group_string(response['Item'].get('custom:vu_groups', '[]'))
        users_amplify_groups = parse_group_string(response['Item'].get('amplify_groups', '[]'))
    
        
        cognito_group_dict = {group: group in users_cognito_groups for group in cogn_groups}
        amplify_group_dict = {group: group in users_amplify_groups for group in amp_groups}
        

        return {
            'statusCode': 200,
            'body': json.dumps({'success': True, 
                                'data' : {
                                    'amplify_groups': amplify_group_dict,
                                    'cognito_groups': cognito_group_dict
                                }})
            }

    except ClientError as e:
        print("Error: ", e.response['Error']['Message'])
        return {
                'statusCode': 500,
                'body': json.dumps({'success': False, 'error': str(e)})
                }
    

def parse_group_string(group_str):
    """ Parses a group string formatted like a list and returns a Python list. """
    if group_str and group_str.startswith('[') and group_str.endswith(']'):
        return [item.strip() for item in group_str[1:-1].split(',')]
    return []
