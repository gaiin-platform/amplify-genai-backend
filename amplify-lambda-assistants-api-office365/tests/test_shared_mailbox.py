#!/usr/bin/env python3
"""
Shared Mailbox Integration Test Script
=======================================

Tests the shared mailbox functionality against a deployed Amplify environment.

Prerequisites:
  1. You have an Amplify API key (from Settings -> API Keys in the Amplify UI)
  2. You have connected the "Shared Email" integration in Settings -> Integrations
  3. You have access to at least one shared mailbox (e.g. amplify@vanderbilt.edu)

Usage:
  python test_shared_mailbox.py

Environment Variables (or edit the constants below):
  AMPLIFY_API_KEY       - Your Amplify API key
  AMPLIFY_API_BASE_URL  - The API base URL (e.g. https://dev-amplify.vanderbilt.edu/api)
  SHARED_MAILBOX_EMAIL  - The shared mailbox email to test against
"""

import json
import os
import sys
import requests
from datetime import datetime

# ============================================================================
# CONFIGURATION - Edit these or set via environment variables
# ============================================================================

API_KEY = os.environ.get("AMPLIFY_API_KEY", "YOUR_AMPLIFY_API_KEY_HERE")
API_BASE_URL = os.environ.get("AMPLIFY_API_BASE_URL", "https://dev-amplify.vanderbilt.edu/api")
SHARED_MAILBOX = os.environ.get("SHARED_MAILBOX_EMAIL", "amplify@vanderbilt.edu")
USER_EMAIL = "maximillian.r.moundas@vanderbilt.edu"

# ============================================================================
# Helper
# ============================================================================

def call_api(endpoint_path, data=None, method="POST"):
    """Call the Amplify API office365 service."""
    url = f"{API_BASE_URL}/microsoft/integrations/{endpoint_path}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    if method == "POST":
        payload = {"data": data or {}}
        response = requests.post(url, headers=headers, json=payload, timeout=30)
    else:
        response = requests.get(url, headers=headers, timeout=30)

    return response


def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def print_result(label, success, data=None, error=None):
    status = "\033[92mPASS\033[0m" if success else "\033[91mFAIL\033[0m"
    print(f"  [{status}] {label}")
    if data and success:
        if isinstance(data, list):
            print(f"         Returned {len(data)} items")
            for item in data[:3]:  # Show first 3
                if isinstance(item, dict):
                    preview = item.get("subject", item.get("displayName", str(item)[:80]))
                    print(f"           - {preview}")
            if len(data) > 3:
                print(f"           ... and {len(data) - 3} more")
        elif isinstance(data, dict):
            for k, v in list(data.items())[:5]:
                print(f"         {k}: {v}")
    if error:
        print(f"         Error: {error}")


# ============================================================================
# TEST 1: Discover shared mailboxes (attempt access to known mailbox)
# ============================================================================

def test_discover_shared_mailbox():
    """
    Graph API doesn't have a 'list shared mailboxes I have access to' endpoint
    for delegated permissions. The practical way to discover access is to attempt
    to list folders on the shared mailbox - if it succeeds, you have access.
    """
    print_section("TEST 1: Discover Shared Mailbox Access")
    print(f"  Attempting to access shared mailbox: {SHARED_MAILBOX}")
    print(f"  (If this succeeds, the user has access to this shared mailbox)\n")

    response = call_api("list_shared_mailbox_folders", {
        "mailbox_email": SHARED_MAILBOX,
        "include_child_folders": False,
    })

    if response.status_code == 200:
        result = response.json()
        if result.get("success"):
            folders = result.get("data", [])
            print_result(
                f"Can access {SHARED_MAILBOX} - found {len(folders)} folders",
                True,
                folders,
            )
            return True
        else:
            print_result(
                f"API returned success=false for {SHARED_MAILBOX}",
                False,
                error=result.get("message", result.get("error", "Unknown")),
            )
            return False
    else:
        print_result(
            f"HTTP {response.status_code} accessing {SHARED_MAILBOX}",
            False,
            error=response.text[:200],
        )
        return False


# ============================================================================
# TEST 2: Read from shared mailbox
# ============================================================================

def test_read_messages():
    """List recent messages from the shared mailbox Inbox."""
    print_section("TEST 2: Read Messages from Shared Mailbox")

    response = call_api("list_shared_mailbox_messages", {
        "mailbox_email": SHARED_MAILBOX,
        "folder_id": "Inbox",
        "top": 5,
        "include_body": False,
    })

    if response.status_code == 200:
        result = response.json()
        if result.get("success"):
            messages = result.get("data", [])
            print_result(
                f"Listed messages from {SHARED_MAILBOX}/Inbox",
                True,
                messages,
            )
            return messages
        else:
            print_result(
                "list_shared_mailbox_messages failed",
                False,
                error=result.get("message", result.get("error")),
            )
            return []
    else:
        print_result(
            f"HTTP {response.status_code}",
            False,
            error=response.text[:200],
        )
        return []


def test_read_single_message(message_id):
    """Get a single message with full body."""
    print(f"\n  --- Get single message (with body) ---")

    response = call_api("get_shared_mailbox_message", {
        "mailbox_email": SHARED_MAILBOX,
        "message_id": message_id,
        "include_body": True,
    })

    if response.status_code == 200:
        result = response.json()
        if result.get("success"):
            msg = result.get("data", {})
            print_result(
                f"Got message: {msg.get('subject', '(no subject)')[:60]}",
                True,
                {
                    "from": msg.get("from", {}).get("address", "unknown"),
                    "received": msg.get("receivedDateTime"),
                    "body_length": len(msg.get("body", "") or ""),
                },
            )
            return True
        else:
            print_result("get_shared_mailbox_message failed", False, error=result.get("message"))
            return False
    else:
        print_result(f"HTTP {response.status_code}", False, error=response.text[:200])
        return False


def test_search_messages():
    """Search for messages in the shared mailbox."""
    print(f"\n  --- Search messages ---")

    response = call_api("search_shared_mailbox_messages", {
        "mailbox_email": SHARED_MAILBOX,
        "search_query": "test",
        "top": 3,
        "include_body": False,
    })

    if response.status_code == 200:
        result = response.json()
        if result.get("success"):
            messages = result.get("data", [])
            print_result(
                f"Search returned {len(messages)} result(s)",
                True,
                messages,
            )
            return True
        else:
            print_result("search_shared_mailbox_messages failed", False, error=result.get("message"))
            return False
    else:
        print_result(f"HTTP {response.status_code}", False, error=response.text[:200])
        return False


def test_list_attachments(message_id):
    """List attachments on a message."""
    print(f"\n  --- List attachments ---")

    response = call_api("get_shared_mailbox_attachments", {
        "mailbox_email": SHARED_MAILBOX,
        "message_id": message_id,
    })

    if response.status_code == 200:
        result = response.json()
        if result.get("success"):
            attachments = result.get("data", [])
            print_result(
                f"Found {len(attachments)} attachment(s)",
                True,
                attachments,
            )
            return True
        else:
            print_result("get_shared_mailbox_attachments failed", False, error=result.get("message"))
            return False
    else:
        print_result(f"HTTP {response.status_code}", False, error=response.text[:200])
        return False


# ============================================================================
# TEST 3: Create a draft in the shared mailbox
# ============================================================================

def test_create_draft():
    """Create a test draft in the shared mailbox Drafts folder."""
    print_section("TEST 3: Create Draft in Shared Mailbox")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"[TEST] Shared Mailbox Draft - {timestamp}"
    body = (
        f"This is a test draft created by the Amplify shared mailbox integration test script.\n\n"
        f"Created at: {timestamp}\n"
        f"Created by: {USER_EMAIL}\n"
        f"Target mailbox: {SHARED_MAILBOX}\n\n"
        f"If you see this draft in the {SHARED_MAILBOX} Drafts folder, the integration is working."
    )

    response = call_api("create_shared_mailbox_draft", {
        "mailbox_email": SHARED_MAILBOX,
        "subject": subject,
        "body": body,
        "to_recipients": [USER_EMAIL],
        "importance": "normal",
        "content_type": "text",
    })

    if response.status_code == 200:
        result = response.json()
        if result.get("success"):
            draft_data = result.get("data", {})
            print_result(
                f"Draft created in {SHARED_MAILBOX}",
                True,
                {
                    "message_id": draft_data.get("message_id", "")[:40] + "...",
                    "subject": draft_data.get("subject"),
                    "isDraft": draft_data.get("isDraft"),
                    "created": draft_data.get("createdDateTime"),
                },
            )
            print(f"\n  >> Check the Drafts folder of {SHARED_MAILBOX} to verify!")
            return True
        else:
            print_result(
                "create_shared_mailbox_draft failed",
                False,
                error=result.get("message", result.get("error")),
            )
            return False
    else:
        print_result(f"HTTP {response.status_code}", False, error=response.text[:200])
        return False


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "="*70)
    print("  AMPLIFY SHARED MAILBOX INTEGRATION TEST")
    print("="*70)
    print(f"\n  API Base URL:     {API_BASE_URL}")
    print(f"  User:             {USER_EMAIL}")
    print(f"  Shared Mailbox:   {SHARED_MAILBOX}")
    print(f"  API Key:          {API_KEY[:8]}...{API_KEY[-4:] if len(API_KEY) > 12 else '****'}")

    if API_KEY == "YOUR_AMPLIFY_API_KEY_HERE":
        print("\n  \033[91mERROR: Please set AMPLIFY_API_KEY environment variable or edit the script.\033[0m")
        sys.exit(1)

    results = {}

    # Test 1: Discover / verify access
    results["discover"] = test_discover_shared_mailbox()

    if not results["discover"]:
        print("\n  \033[93mWARNING: Cannot access shared mailbox. Remaining tests will likely fail.\033[0m")
        print(f"  Possible causes:")
        print(f"    - User {USER_EMAIL} doesn't have access to {SHARED_MAILBOX}")
        print(f"    - The 'Shared Email' integration is not connected in Amplify")
        print(f"    - The API key is invalid or expired")
        print(f"    - Mail.Read permission was not granted")

    # Test 2: Read operations
    messages = test_read_messages()
    results["read_messages"] = len(messages) > 0

    if messages:
        first_msg_id = messages[0].get("id")
        if first_msg_id:
            results["read_single"] = test_read_single_message(first_msg_id)
            results["attachments"] = test_list_attachments(first_msg_id)
        else:
            results["read_single"] = False
            results["attachments"] = False

    results["search"] = test_search_messages()

    # Test 3: Write draft
    results["create_draft"] = test_create_draft()

    # Summary
    print_section("SUMMARY")
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed

    for test_name, success in results.items():
        status = "\033[92mPASS\033[0m" if success else "\033[91mFAIL\033[0m"
        print(f"  [{status}] {test_name}")

    print(f"\n  Total: {total} | Passed: {passed} | Failed: {failed}")

    if failed == 0:
        print(f"\n  \033[92mAll tests passed! Shared mailbox integration is working.\033[0m\n")
    else:
        print(f"\n  \033[91m{failed} test(s) failed. See details above.\033[0m\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
