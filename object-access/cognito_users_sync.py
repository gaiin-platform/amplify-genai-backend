import boto3
from datetime import datetime
import os

def sync_users_to_dynamo(event, context):
    cognito = boto3.client('cognito-idp')
    dynamodb = boto3.resource('dynamodb')
    user_pool_id = os.environ['COGNITO_USER_POOL_ID']
    dynamo_table_name = os.environ['COGNITO_USERS_DYNAMODB_TABLE']
    dynamo_table = dynamodb.Table(dynamo_table_name)
    
    pagination_token = None
    
    while True:
        args = {'UserPoolId': user_pool_id}
        if pagination_token:
            args['PaginationToken'] = pagination_token
            
        response = cognito.list_users(**args)
        
        for user in response['Users']:
            user_attributes = {attr['Name']: attr['Value'] for attr in user['Attributes']}
            user_id = user_attributes.get('email')  # Use email as the user_id
            
            if user_id:  # Ensure that user_id (email) is not None
                filtered_attributes = {
                    'user_id': user_id,
                    'family_name': user_attributes.get('family_name'),
                    'given_name': user_attributes.get('given_name'),
                    'custom:groups': user_attributes.get('custom:groups'),
                    'updated_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
                }
                
                existing_user = dynamo_table.get_item(Key={'user_id': user_id}).get('Item')
                if existing_user:
                    if any(existing_user.get(attr) != filtered_attributes.get(attr) for attr in filtered_attributes):
                        filtered_attributes.pop('user_id', None)
                        # update dynamo expression can not handle ':' so we need to replace with a placeholder '_'
                        dynamo_table.update_item(
                            Key={'user_id': user_id},
                            UpdateExpression='SET ' + ', '.join(f'#{k.replace(":", "_")}=:{k.replace(":", "_")}' for k in filtered_attributes),
                            ExpressionAttributeNames={f'#{k.replace(":", "_")}': k for k in filtered_attributes},
                            ExpressionAttributeValues={f':{k.replace(":", "_")}': v for k, v in filtered_attributes.items()}
                        )
                        print(f"Updated user: {user_id}")
                else:
                    dynamo_table.put_item(Item=filtered_attributes)
                    print(f"Created user: {user_id}")
            else:
                print("No email found for the user, skipping...")

        pagination_token = response.get('PaginationToken')
        if not pagination_token:
            break

    return {
        'statusCode': 200,
        'body': 'Sync completed successfully'
    }