import boto3
from botocore.exceptions import ClientError
from common.validate import validated
import os

sender_email = 'amplify@mail.vanderbilt.ai'


@validated(op="send_email")
def send_email(event, context, current_user, name, data):
    try:
        # Create a new SES client
        ses_client = boto3.client('ses')
        print(data)
        data = data['data']
        email_to = data['email_to']
        email_subject = data['email_subject']
        email_body = data['email_body']

        # Define the sender email (must be verified in SES)
        email_from = sender_email

        # Send the email
        response = ses_client.send_email(
            Source=email_from,
            Destination={
                'ToAddresses': [email_to],
            },
            Message={
                'Subject': {
                    'Data': email_subject,
                },
                'Body': {
                    'Text': {
                        'Data': email_body,
                    },
                },
            },
        )
        
        print(f"Email sent! Message ID: {response['MessageId']}")
        return True
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return False