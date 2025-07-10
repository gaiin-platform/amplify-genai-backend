import json
import uuid
from datetime import datetime, timezone
import base64
import hashlib


def generate_ses_event(sender, receiver, body, subject, cc=None, bcc=None):
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # Create a simple email structure
    email_content = f"""From: {sender}
To: {receiver}
Subject: {subject}
Date: {timestamp}

{body}
"""
    # Encode the email content
    encoded_content = base64.b64encode(email_content.encode()).decode()

    ses_notification = {
        "notificationType": "Received",
        "mail": {
            "timestamp": timestamp,
            "source": sender,
            "messageId": message_id,
            "destination": [receiver],
            "headersTruncated": False,
            "headers": [
                {"name": "From", "value": f"{sender}"},
                {"name": "To", "value": f"{receiver}"},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": f"<{message_id}>"},
            ],
            "commonHeaders": {"from": [sender], "to": [receiver], "subject": subject},
        },
        "receipt": {
            "timestamp": timestamp,
            "processingTimeMillis": 1000,
            "recipients": [receiver],
            "spamVerdict": {"status": "PASS"},
            "virusVerdict": {"status": "PASS"},
            "spfVerdict": {"status": "PASS"},
            "dkimVerdict": {"status": "PASS"},
            "dmarcVerdict": {"status": "PASS"},
            "action": {
                "type": "SNS",
                "topicArn": "arn:aws:sns:us-east-1:514391678313:vu-amplify-agent-loop-dev-email-topic",
            },
        },
        "content": encoded_content,
    }

    sns_message = {
        "Type": "Notification",
        "MessageId": str(uuid.uuid4()),
        "TopicArn": "arn:aws:sns:us-east-1:514391678313:vu-amplify-agent-loop-dev-email-topic",
        "Subject": "Amazon SES Email Receipt Notification",
        "Message": json.dumps(ses_notification),
        "Timestamp": timestamp,
        "SignatureVersion": "1",
        "Signature": "dummy_signature",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-9c6465fa7f48f5cacd23014631ec1136.pem",
        "UnsubscribeURL": "https://sns.us-east-1.amazonaws.com/?Action=Unsubscribe&SubscriptionArn=arn:aws:sns:us-east-1:514391678313:vu-amplify-agent-loop-dev-email-topic:8ca6f471-7b1f-4803-af0e-b6487ab1a04e",
    }

    body_content = json.dumps(sns_message)

    ses_event = {
        "Records": [
            {
                "messageId": str(uuid.uuid4()),
                "receiptHandle": "AQEB"
                + "".join([str(uuid.uuid4()).replace("-", "") for _ in range(4)]),
                "body": body_content,
                "attributes": {
                    "ApproximateReceiveCount": "1",
                    "SentTimestamp": str(
                        int(datetime.now(timezone.utc).timestamp() * 1000)
                    ),
                    "SenderId": "AIDAIT2UOQQY3AUEKVGXU",
                    "ApproximateFirstReceiveTimestamp": str(
                        int(datetime.now(timezone.utc).timestamp() * 1000)
                    ),
                },
                "messageAttributes": {},
                "md5OfBody": hashlib.md5(body_content.encode()).hexdigest(),
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-1:514391678313:vu-amplify-agent-loop-dev-agent-queue",
                "awsRegion": "us-east-1",
            }
        ]
    }

    # Add CC and BCC if provided
    if cc:
        ses_notification["mail"]["commonHeaders"]["cc"] = cc
        ses_notification["mail"]["headers"].append(
            {"name": "CC", "value": ", ".join(cc)}
        )
    if bcc:
        ses_notification["mail"]["commonHeaders"]["bcc"] = bcc
        ses_notification["mail"]["headers"].append(
            {"name": "BCC", "value": ", ".join(bcc)}
        )

    return ses_event
