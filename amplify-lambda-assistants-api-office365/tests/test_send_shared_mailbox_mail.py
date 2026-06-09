#!/usr/bin/env python3
"""
Quick test: send an email as the shared mailbox to preston.m.horne@vanderbilt.edu
Runs against the local dev server at localhost:3015.
"""

import requests
from datetime import datetime

API_KEY = "amp-v1-1234567890abcdef"  # Replace with your actual API key
API_BASE_URL = "http://localhost:3015/dev"
SHARED_MAILBOX = "amplify@vanderbilt.edu"
TO = "preston.m.horne@vanderbilt.edu"


def call_api(endpoint, data):
    url = f"{API_BASE_URL}/microsoft/integrations/{endpoint}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json={"data": data}, timeout=120)
    return response


timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

print(f"Sending mail from {SHARED_MAILBOX} to {TO}...")

response = call_api("send_shared_mailbox_mail", {
    "mailbox_email": SHARED_MAILBOX,
    "subject": f"[TEST] Shared Mailbox Send - {timestamp}",
    "body": (
        f"This is a test email sent via the Amplify shared mailbox integration.\n\n"
        f"Sent at: {timestamp}\n"
        f"From (shared mailbox): {SHARED_MAILBOX}\n"
        f"To: {TO}\n\n"
        f"If you received this, the send_shared_mailbox_mail endpoint is working correctly."
    ),
    "to_recipients": [TO],
    "importance": "normal",
    "content_type": "text",
})

print(f"Status: {response.status_code}")
print(f"Response: {response.text}")
