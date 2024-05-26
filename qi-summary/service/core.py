import json
import os
import uuid
from common.validate import validated
from botocore.exceptions import BotoCoreError, ClientError
import boto3
from common.cognito_user_groups import get_user_cognito_groups


@validated(op="conversation_upload")
def upload_conversation(event, context, current_user, name, data):
    s3 = boto3.client('s3')
    qi_bucket = os.environ['QI_FILES_BUCKET_NAME']

    access_token = data['access_token']
    qi_data = data['data']
    qi = qi_data["qiData"]
    if (qi['includeUser']): qi['nonAnonymousUser'] = current_user

    qi.pop('includeUser', None)


    cognito_groups = get_user_cognito_groups(access_token)
    print(current_user, " belongs to groups: ", cognito_groups)
    # call to get cognito user groups 
    # we want to separate out the SoN #Amplify_Dev_SoN
    path = 'Conversations/' 
    if ("Amplify_Dev_SoN" in cognito_groups): path += "SoN/"
    file_key = path + str(uuid.uuid1())   
    try:
        s3.put_object(Bucket=qi_bucket,
                      Key=file_key,
                      Body=json.dumps(qi_data))
        return {
                'statusCode': 200,
                'body': json.dumps({'success' : True, 'message': "Succesfully uploaded conversation to s3"})
                }
    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
                'statusCode': 404,
                'body': json.dumps({'success' : False, 'message': "Failed to uploaded conversation to s3", 'error': str(e)})
                }
