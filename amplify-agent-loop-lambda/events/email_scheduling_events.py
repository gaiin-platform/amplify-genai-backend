
import json
from typing import Dict, Any
from events.event_handler import MessageHandler, SPECIALIZED_EMAILS
from events.ses_message_functions import (
    extract_email_body_and_attachments,
    lookup_username_from_email,
    extract_destination_emails,
    is_ses_message
)
from pycommon.logger import getLogger
from pycommon.db_utils import convert_floats_to_decimal
import boto3
import os
import requests
from datetime import timezone
from delegation.api_keys import create_agent_event_api_key

logger = getLogger("scheduling_email_events")



class SESSchedulingMessageHandler(MessageHandler):
    """Handler for emails sent to schedule email address"""

    # Get scheduler email from registry (single source of truth)
    SCHEDULER_EMAIL = SPECIALIZED_EMAILS["SCHEDULER"]

    def is_agent_loop_event(self, event: Dict[str, Any] = None) -> bool:
        """Scheduling events should not trigger agent loop execution"""
        return False

    def can_handle(self, message: Dict[str, Any]) -> bool:
        """Check if message is an SES event sent to the scheduler email"""
        try:
            # First check if it's a valid SES message
            if not is_ses_message(message):
                return False

            # Check if destination email is schedule email
            destination_emails = extract_destination_emails(message)
            if self.SCHEDULER_EMAIL in destination_emails:
                logger.info("Scheduling email detected: %s", self.SCHEDULER_EMAIL)
                return True

            return False

        except Exception as e:
            logger.error("Error in SESSchedulingMessageHandler.can_handle: %s", e)
            return False

    def process(self, message: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Process scheduling email and create agent event"""
        logger.info("Processing scheduling email")

        try:
            ses_content = json.loads(message["Message"])
            mail_data = ses_content["mail"]
            common_headers = mail_data.get("commonHeaders", {})

            # Extract sender and recipients
            source_email = mail_data["source"].lower()
            destination_emails = mail_data["destination"]

            logger.info("Scheduling email from: %s to: %s", source_email, destination_emails)

            # Extract email body and attachments
            parsed_email = extract_email_body_and_attachments(ses_content)
            email_body = parsed_email.get("body_plain") or parsed_email.get("body_html", "")
            email_body_html = parsed_email.get("body_html", "")

            # Determine the user from the sender's email
            sender_username = lookup_username_from_email(source_email)

            ### PROCESSING LOGIC  ###
            # This implements the complete SES → Analysis → Draft → Outlook flow
        

            # Get API Gateway base URL from environment
            stage = os.environ.get('STAGE', 'dev')
            amp_base_url = os.environ.get('API_BASE_URL')
           
            # TARGET  #dev-scheduling.dev-amplify.vanderbilt.ai
            api_base_url = amp_base_url.replace("-api.", "-scheduling.") if amp_base_url else None

            if not api_base_url:
                logger.error("API_BASE_URL is not configured in environment variables")
                return {"result": None, "error": "configuration_error"}

            # Extract sender name if available
            sender_name = common_headers.get("from", [source_email])[0]
            if '<' in sender_name:
                # Format: "John Doe <john@example.com>"
                sender_name = sender_name.split('<')[0].strip()

            logger.info("📧 Starting SES scheduling flow for user: %s", sender_username)
            logger.info("   From: %s <%s>", sender_name, source_email)
            logger.info("   Subject: %s", common_headers.get('subject', 'No subject'))

            # ===========================================
            # STEP 0: GET SQS API KEY FOR USER
            # ===========================================
            logger.info("🔑 Step 0: Retrieving/creating API key for user...")

            try:
                from datetime import datetime
                import uuid
                from delegation.api_keys import get_api_key_directly_by_id

                # Get DynamoDB table to check for existing API key ID
                dynamodb = boto3.resource('dynamodb')
                settings_table_name = os.environ.get('AI_SCHEDULER_STORAGE_TABLE')
                settings_table = dynamodb.Table(settings_table_name)

                # Try to get existing API key ID (NOT the actual key!)
                key_response = settings_table.get_item(
                    Key={
                        'user_id': sender_username,
                        'storage_type': 'sqsaccess'
                    }
                )

                existing_key_item = key_response.get('Item')

                existing_key_data = existing_key_item.get('data', {}) if existing_key_item else {}
                # Support both new format (data.api_key_id) and legacy (top-level api_key_id)
                stored_api_key_id = existing_key_data.get('api_key_id') or (existing_key_item.get('api_key_id') if existing_key_item else None)
                if stored_api_key_id:
                    # We have an API key ID - retrieve the actual key from API keys table
                    api_key_id = stored_api_key_id
                    logger.info("✅ Found existing API key ID: %s", api_key_id)

                    # Get the actual API key using the ID
                    api_result = get_api_key_directly_by_id(api_key_id)
                    if not api_result["success"]:
                        logger.error("❌ Failed to retrieve API key: %s", api_result.get('message'))
                        raise Exception(f"API key retrieval failed: {api_result.get('message')}")

                    access_token = api_result["apiKey"]
                    logger.info("✅ Retrieved actual API key from API keys table")

                else:
                    # No key exists - need to create one
                    logger.info("⚠️ No API key found, creating new one...")

                    # Step 1: Create an API Key for this event
                    tag = "scheduling_email_agent"
                    description = "API key for AI scheduling assistant - auto-created for email forwarding"

                    # Get account ID (use "default" if not available)
                    account = "default"

                    api_key_response = create_agent_event_api_key(
                        user=sender_username,
                        agent_event_name=tag,
                        account=account,
                        description=description,
                        purpose="email_event",
                    )

                    if not api_key_response or not api_key_response.get("success"):
                        raise Exception("Failed to create API key")

                    # Extract API Key ID (NOT the actual key!)
                    api_key_id = api_key_response["data"]["id"]
                    logger.info("✅ Created new API key with ID: %s", api_key_id)

                    # Get the actual API key using the ID
                    api_result = get_api_key_directly_by_id(api_key_id)
                    if not api_result["success"]:
                        raise Exception(f"Failed to retrieve newly created API key: {api_result.get('message')}")

                    access_token = api_result["apiKey"]

                    # Store ONLY the API key ID in DynamoDB (NOT the actual key!)
                    now = datetime.now(timezone.utc).isoformat()
                    settings_table.put_item(
                        Item={
                            'user_id': sender_username,
                            'storage_type': 'sqsaccess',
                            'created_at': now,
                            'updated_at': now,
                            'data': {
                                'api_key_id': api_key_id,
                                'purpose': 'scheduling_agent'
                            }
                        }
                    )
                    logger.info("✅ Stored API key ID in user settings (actual key is in API keys table)")

            except Exception as e:
                logger.error("❌ Failed to get/create API key: %s", e, exc_info=True)
                # Fallback - will cause auth failures but won't crash
                access_token = 'amp-fallback-key'
                api_key_id = None  # Must be defined for Step 0.7

            # ===========================================
            # STEP 0.7: SMART REPLY-TO SENDER EXTRACTION
            # ===========================================
            # For forwarded emails the true draft recipient is the ORIGINAL sender
            # buried in the chain — not source_email (who forwarded to schedule@).
            # Primary path: ask the agent LLM (smarter, handles complex chains).
            # Fallback: regex — scan all From: headers and take the deepest one.
            logger.info("📨 Step 0.7: Extracting true reply-to sender...")

            reply_to_email = source_email  # default: reply to whoever forwarded
            reply_to_name = sender_name    # default: their display name

            _forward_markers = [
                'forwarded message', 'begin forwarded message',
                'original message', '-----forwarded', 'fwd:', 'fw:'
            ]
            _body_lower = (email_body or '').lower()
            _subject_lower = common_headers.get('subject', '').lower()
            _looks_forwarded = (
                any(m in _body_lower for m in _forward_markers) or
                _subject_lower.startswith('fwd:') or
                _subject_lower.startswith('fw:')
            )

            if _looks_forwarded:
                logger.info("   📧 Forwarded email detected — attempting smart sender extraction")

                # --- Attempt 1: LLM extraction (preferred) ---
                try:
                    from agent.prompt import create_llm, Prompt
                    from pycommon.api.models import get_default_models

                    _default_models = get_default_models(access_token)
                    _extract_model = _default_models.get('agent_model')

                    if _extract_model:
                        _extract_llm = create_llm(
                            access_token,
                            _extract_model,
                            sender_username,
                            {'account_id': 'general_account', 'api_key_id': api_key_id, 'rate_limit': None},
                            {'purpose': 'sender_extraction'}
                        )
                        _extract_prompt = Prompt(
                            messages=[
                                {
                                    'role': 'system',
                                    'content': (
                                        'You are an email routing assistant. Given an email body that may be a '
                                        'forwarded chain, identify the ORIGINAL sender — the person who wrote '
                                        'the scheduling request at the bottom of the chain (not whoever forwarded '
                                        'it). Respond ONLY with valid JSON: '
                                        '{"reply_to_email": "email@example.com", "reply_to_name": "First Last"} '
                                        'If you cannot determine the original sender, respond with: '
                                        '{"reply_to_email": null, "reply_to_name": null}'
                                    )
                                },
                                {
                                    'role': 'user',
                                    'content': (email_body or '')[:4000]
                                }
                            ]
                        )
                        _llm_raw = _extract_llm(_extract_prompt)

                        # Parse JSON from LLM response
                        import re as _re
                        _json_match = _re.search(r'\{[^{}]+\}', _llm_raw or '')
                        if _json_match:
                            _parsed = json.loads(_json_match.group())
                            _llm_email = _parsed.get('reply_to_email')
                            _llm_name = _parsed.get('reply_to_name')
                            _valid_email = (
                                _llm_email and
                                '@' in str(_llm_email) and
                                str(_llm_email).lower() not in ('null', 'none', '')
                            )
                            if _valid_email:
                                reply_to_email = str(_llm_email).strip().lower()
                                _name_raw = str(_llm_name) if _llm_name else None
                                reply_to_name = (
                                    _name_raw.strip()
                                    if _name_raw and _name_raw.lower() not in ('null', 'none')
                                    else reply_to_email.split('@')[0]
                                )
                                logger.info("   ✅ LLM extracted reply-to: %s (%s)", reply_to_email, reply_to_name)
                            else:
                                logger.info("   ℹ️ LLM returned null reply_to — trying regex fallback")
                    else:
                        logger.warning("   ⚠️ No agent_model configured — skipping LLM extraction")

                except Exception as _llm_err:
                    logger.warning("   ⚠️ LLM sender extraction failed: %s — trying regex fallback", _llm_err)

                # --- Attempt 2: Regex fallback (deepest From: in the chain) ---
                if reply_to_email == source_email:
                    try:
                        import re as _re
                        _from_pattern = _re.compile(
                            r'^from:\s*(?:.*?<([^>@\s]+@[^>@\s]+)>|([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}))',
                            _re.IGNORECASE | _re.MULTILINE
                        )
                        _from_emails = [
                            (m[0] or m[1]).strip().lower()
                            for m in _from_pattern.findall(email_body or '')
                            if (m[0] or m[1]).strip()
                        ]
                        if _from_emails:
                            _deepest = _from_emails[-1]
                            if _deepest != source_email.lower():
                                reply_to_email = _deepest
                                reply_to_name = reply_to_email.split('@')[0]
                                logger.info("   ✅ Regex fallback extracted reply-to: %s", reply_to_email)
                            else:
                                logger.info("   ℹ️ Regex: deepest sender matches source_email — keeping as-is")
                    except Exception as _regex_err:
                        logger.warning("   ⚠️ Regex fallback also failed: %s", _regex_err)
            else:
                logger.info("   ℹ️ Not a forwarded email — reply-to = envelope sender: %s", source_email)

            logger.info("   📨 Final reply-to: %s (%s)", reply_to_email, reply_to_name)

            # ===========================================
            # STEP 1: ANALYZE EMAIL (inline LLM classification)
            # ===========================================
            logger.info("🔍 Step 1: Analyzing email intent and extracting meeting details...")

            try:
                from agent.prompt import create_llm, Prompt
                from pycommon.api.models import get_default_models as _get_models

                _classify_models = _get_models(access_token)
                _classify_model = _classify_models.get('cheapest_model') or _classify_models.get('agent_model')

                if not _classify_model:
                    logger.error("❌ Could not fetch model for classification")
                    return {"result": None, "error": "model_fetch_failed"}

                _classify_llm = create_llm(
                    access_token,
                    _classify_model,
                    sender_username,
                    {'account_id': 'general_account', 'api_key_id': api_key_id, 'rate_limit': None},
                    {'purpose': 'email_classification'}
                )

                _classify_system = """You are an AI assistant that classifies emails as meeting/scheduling requests or not.

A MEETING REQUEST email (respond YES):
- Explicitly asks to schedule, meet, call, or set up a meeting
- Proposes specific times or asks for availability
- Contains phrases like "let's meet", "can we schedule", "are you available"
- Requests calendar time or face-to-face/video interaction
- Announces or reminds about an upcoming scheduled meeting/training/session (even if already scheduled)
- Invites to events, webinars, training sessions, or calls with specific times

NOT a meeting request (respond NO):
- Status updates, project updates, or technical discussions
- Questions about code, bugs, or information requests
- Task assignments or follow-ups WITHOUT meeting times
- Social messages, greetings, or casual conversation
- Newsletters or marketing emails WITHOUT event invitations
- Feedback or survey requests

Respond with JSON only:
{
  "is_meeting_request": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}"""

                _classify_user = f"""Classify this email:

Subject: {common_headers.get('subject', '')}
From: {reply_to_email}
Preview: {email_body[:300]}

Is this a meeting/scheduling request?"""

                _classify_prompt = Prompt(messages=[
                    {'role': 'system', 'content': _classify_system},
                    {'role': 'user', 'content': _classify_user}
                ])

                _classify_response = _classify_llm(_classify_prompt)

                # Parse JSON response
                import json as _json
                try:
                    analysis_data = _json.loads(_classify_response)
                except (_json.JSONDecodeError, TypeError):
                    _start = (_classify_response or '').find('{')
                    _end = (_classify_response or '').rfind('}') + 1
                    if _start >= 0 and _end > _start:
                        analysis_data = _json.loads(_classify_response[_start:_end])
                    else:
                        analysis_data = {'is_meeting_request': False, 'confidence': 0.5, 'reasoning': 'Parse error'}

                is_meeting_request = analysis_data.get('is_meeting_request', False)
                confidence = float(analysis_data.get('confidence', 0.0))
                reasoning = analysis_data.get('reasoning', '')

                # Map classify response to intent/meeting_details shape the rest of the pipeline expects
                intent = 'new_request' if is_meeting_request else 'no_action'
                meeting_details = {}
                requires_review = confidence < 0.8

                logger.info("✅ Analysis complete: intent=%s, confidence=%.2f, is_meeting_request=%s", intent, confidence, is_meeting_request)

                # ===========================================
                # STEP 1.5: EARLY-RETURN ON NON-SCHEDULING INTENTS
                # ===========================================
                NON_SCHEDULING_INTENTS = ['no_action', 'informational', 'spam', 'out_of_scope', 'thank_you', 'follow_up_no_action']
                if intent in NON_SCHEDULING_INTENTS:
                    logger.info("⏭️ Non-scheduling intent detected: %s — skipping processing", intent)
                    _result = {"skipped": True, "reason": "non_scheduling_intent", "intent": intent, "confidence": confidence}
                    if is_test_run and test_request_id:
                        try:
                            _now = datetime.now(timezone.utc).isoformat()
                            settings_table.update_item(
                                Key={'user_id': sender_username, 'storage_type': f'test#{test_request_id}'},
                                UpdateExpression='SET #data.#status = :s, #data.#result = :r, #data.processing.completed_at = :c, updated_at = :u',
                                ExpressionAttributeNames={'#data': 'data', '#status': 'status', '#result': 'result'},
                                ExpressionAttributeValues={':s': 'completed', ':r': {'action_taken': 'declined', 'confidence': confidence, 'reasoning': f'Non-scheduling intent: {intent}'}, ':c': _now, ':u': _now}
                            )
                            logger.info("   ✅ Test record updated to completed (non_scheduling_intent)")
                        except Exception as _te:
                            logger.warning("   ⚠️ Failed to update test record: %s", _te)
                    return {"result": _result}

            except Exception as e:
                logger.error("❌ Failed to classify email: %s", e, exc_info=True)
                return {"result": None, "error": "lambda_invocation_failed"}

            # ===========================================
            # STEP 2: GET USER SETTINGS & SIGNATURES (DynamoDB)
            # ===========================================
            logger.info("⚙️ Step 2: Fetching user settings and signatures...")

            dynamodb = boto3.resource('dynamodb')
            settings_table = dynamodb.Table(os.environ.get('AI_SCHEDULER_STORAGE_TABLE'))

            try:
                # Get user_settings from data column (NEW SCHEMA)
                settings_response = settings_table.get_item(Key={
                    'user_id': sender_username,
                    'storage_type': 'user_settings'  # CRITICAL: Must include sort key!
                })
                user_doc = settings_response.get('Item', {})
                user_settings = user_doc.get('data', {})

                # Get signatures from data column for draft generation
                sig_response = settings_table.get_item(Key={
                    'user_id': sender_username,
                    'storage_type': 'signatures'
                })
                sig_doc = sig_response.get('Item', {})
                signatures = sig_doc.get('data', [])

                # Find primary signature
                primary_signature = None
                for sig in signatures:
                    if sig.get('is_primary'):
                        primary_signature = sig
                        break
                if not primary_signature and signatures:
                    primary_signature = signatures[0]

                # Default settings if user hasn't configured yet
                if not user_settings:
                    logger.info("⚠️ No user settings found, using safest defaults (draft_only mode)")
                    user_settings = {
                        'automation_level': 'draft_only',
                        'auto_send_rules': {
                            'enabled': False,
                            'confidence_threshold': 0.95,
                            'excluded_days': [],
                            'require_review_for': ['new_request', 'reschedule', 'cancel', 'accept']
                        },
                        'calendar_automation': {
                            'mode': 'never',
                            'auto_decline_mode': 'never',
                            'auto_accept_invites': 'never',
                            'reschedule_mode': 'never',
                            'cancellation_mode': 'manual',
                            'event_status': 'tentative',
                            'include_ccd_recipients': False
                        }
                    }

                # ===========================================
                # STEP 2.0: DETECT TEST TAG — override settings if this is a test email
                # ===========================================
                is_test_run = False
                test_request_id = None
                TEST_TAG_PREFIX = "<!--[AMPLIFY_TEST_TAG]"
                TEST_TAG_SUFFIX = "[/AMPLIFY_TEST_TAG]-->"
                logger.info("🔎 Step 2.0: Scanning email body for test tag (plain: %d chars, html: %d chars)...", len(email_body), len(email_body_html))
                tag_start = email_body.find(TEST_TAG_PREFIX)
                _scan_body = email_body  # which body surface we found the tag in
                if tag_start < 0 and email_body_html:
                    # HTML comments are stripped from plain text — fall back to HTML body
                    tag_start = email_body_html.find(TEST_TAG_PREFIX)
                    if tag_start >= 0:
                        _scan_body = email_body_html
                        logger.info("   ℹ️ Tag not in plain text — found in HTML body at position %d", tag_start)
                if tag_start < 0:
                    logger.info("   ℹ️ No test tag found in plain or HTML body — processing as real email with user settings")
                else:
                    logger.info("   ✅ Test tag found at position %d", tag_start)
                    tag_end = _scan_body.find(TEST_TAG_SUFFIX, tag_start)
                    if tag_end < 0:
                        logger.warning("   ⚠️ Test tag prefix found but suffix missing — malformed tag, ignoring")
                    else:
                        try:
                            tag_json = _scan_body[tag_start + len(TEST_TAG_PREFIX):tag_end]
                            logger.info("   📋 Tag JSON: %s", tag_json)
                            tag_data = json.loads(tag_json)
                            test_request_id = tag_data.get('test_request_id')
                            # Strip the tag from the plain-text body so it doesn't appear in drafts.
                            # (If the tag was only in HTML, plain body is already clean.)
                            plain_tag_start = email_body.find(TEST_TAG_PREFIX)
                            if plain_tag_start >= 0:
                                plain_tag_end = email_body.find(TEST_TAG_SUFFIX, plain_tag_start)
                                if plain_tag_end >= 0:
                                    email_body = (email_body[:plain_tag_start] + email_body[plain_tag_end + len(TEST_TAG_SUFFIX):]).strip()
                            logger.info("   ✂️ Tag stripped from body. New body length: %d chars", len(email_body))

                            if not test_request_id:
                                logger.warning("   ⚠️ Tag parsed but no test_request_id found in: %s", tag_data)
                            else:
                                logger.info("   🔑 test_request_id=%s — looking up test record in DynamoDB...", test_request_id)
                                # Look up the test# record to get test_settings
                                test_record_response = settings_table.get_item(Key={
                                    'user_id': sender_username,
                                    'storage_type': f'test#{test_request_id}'
                                })
                                test_record = test_record_response.get('Item')
                                if not test_record:
                                    logger.warning("   ⚠️ No test# record found for user=%s, test_request_id=%s — using real user settings", sender_username, test_request_id)
                                else:
                                    test_data = test_record.get('data', {})
                                    test_settings = test_data.get('test_settings', {})
                                    if not test_settings:
                                        logger.warning("   ⚠️ Test record found but test_settings is empty — using real user settings")
                                    else:
                                        # Override user_settings with test_settings for this run
                                        user_settings = {
                                            'automation_level': test_settings.get('automation_level', user_settings.get('automation_level', 'draft_only')),
                                            'auto_send_rules': test_settings.get('auto_send_rules', user_settings.get('auto_send_rules', {})),
                                            'calendar_automation': test_settings.get('calendar_automation', user_settings.get('calendar_automation', {})),
                                        }
                                        is_test_run = True
                                        logger.info("   🧪 TEST SETTINGS APPLIED:")
                                        logger.info("      automation_level=%s", user_settings.get('automation_level'))
                                        logger.info("      calendar_automation.mode=%s", user_settings.get('calendar_automation', {}).get('mode', 'never'))
                                        logger.info("      auto_send_rules.enabled=%s", user_settings.get('auto_send_rules', {}).get('enabled', False))
                        except Exception as _te:
                            logger.warning("   ⚠️ Failed to parse test tag: %s", _te)

                automation_level = user_settings.get('automation_level', 'draft_only')
                logger.info("✅ User settings loaded: automation_level=%s%s", automation_level, " [TEST]" if is_test_run else "")
                logger.info("   Signatures found: %d (primary: %s)",
                           len(signatures),
                           primary_signature.get('name') if primary_signature else 'None')

                # ===========================================
                # STEP 2.1: BLOCKED SENDER CHECK
                # ===========================================
                blocked_senders = user_settings.get('sender_filters', {}).get('blocked', [])
                if blocked_senders:
                    sender_domain = source_email.split('@')[-1] if '@' in source_email else ''
                    is_blocked = any(
                        source_email.lower() == b.lower() or sender_domain.lower() == b.lower().lstrip('@')
                        for b in blocked_senders
                    )
                    if is_blocked:
                        logger.info("🚫 Sender %s is in blocked list — skipping processing", source_email)
                        _result = {"skipped": True, "reason": "blocked_sender", "sender_email": reply_to_email}
                        if is_test_run and test_request_id:
                            try:
                                _now = datetime.now(timezone.utc).isoformat()
                                settings_table.update_item(
                                    Key={'user_id': sender_username, 'storage_type': f'test#{test_request_id}'},
                                    UpdateExpression='SET #data.#status = :s, #data.#result = :r, #data.processing.completed_at = :c, updated_at = :u',
                                    ExpressionAttributeNames={'#data': 'data', '#status': 'status', '#result': 'result'},
                                    ExpressionAttributeValues={':s': 'completed', ':r': {'action_taken': 'declined', 'confidence': 0.0, 'reasoning': f'Sender blocked: {reply_to_email}'}, ':c': _now, ':u': _now}
                                )
                                logger.info("   ✅ Test record updated to completed (blocked_sender)")
                            except Exception as _te:
                                logger.warning("   ⚠️ Failed to update test record: %s", _te)
                        return {"result": _result}

            except Exception as e:
                logger.error("❌ Failed to fetch user settings: %s", e, exc_info=True)
                # Continue with safest defaults on error
                user_settings = {
                    'automation_level': 'draft_only',
                    'auto_send_rules': {
                        'enabled': False,
                        'confidence_threshold': 0.95,
                        'excluded_days': [],
                        'require_review_for': ['new_request', 'reschedule', 'cancel', 'accept']
                    },
                    'calendar_automation': {
                        'mode': 'never',
                        'auto_decline_mode': 'never',
                        'auto_accept_invites': 'never',
                        'reschedule_mode': 'never',
                        'cancellation_mode': 'manual',
                        'event_status': 'tentative',
                        'include_ccd_recipients': False
                    }
                }
                signatures = []
                primary_signature = None

            # ===========================================
            # STEP 2.5: CHECK CALENDAR AVAILABILITY (HTTP API call)
            # ===========================================
            logger.info("📅 Step 2.5: Checking calendar availability...")

            calendar_slots = []
            try:
                # Only check calendar if intent requires scheduling
                if intent in ['schedule_new', 'propose_times', 'check_availability', 'new_request', 'reschedule', 'calendar_invite']:
                    duration_minutes = meeting_details.get('duration_minutes', 30)

                    # Call scheduling app's availability endpoint (uses Microsoft Graph + sophisticated algorithms)
                    availability_response = requests.post(
                        f'{api_base_url}/scheduling/check-availability',
                        headers={
                            'Authorization': f'Bearer {access_token}',
                            'Content-Type': 'application/json'
                        },
                        json={
                            'data': {
                                'user_id': sender_username,
                                'duration_minutes': duration_minutes,
                                'days_ahead': 14,  # Check next 2 weeks
                                'business_hours_only': user_settings.get('business_hours', {}).get('enabled', True)
                            }
                        },
                        timeout=30
                    )

                    if availability_response.status_code == 200:
                        availability_result = availability_response.json()
                        if availability_result.get('success'):
                            calendar_slots = availability_result['data'].get('available_slots', [])
                            logger.info("✅ Found %d available time slots", len(calendar_slots))
                    else:
                        logger.warning("⚠️ Calendar check returned %d, continuing without availability",
                                     availability_response.status_code)

            except Exception as e:
                logger.warning("⚠️ Calendar availability check failed: %s", e)
                # Continue without calendar - draft can still be generated

            # ===========================================
            # STEP 2.6: DETERMINE AUTO-SEND ELIGIBILITY
            # ===========================================
            logger.info("🤖 Step 2.6: Checking automation rules...")

            automation_level = user_settings.get('automation_level', 'draft_only')
            auto_send_rules = user_settings.get('auto_send_rules', {})
            calendar_automation_settings = user_settings.get('calendar_automation', {})

            # If automation_level is 'off', check whether calendar automation is still active.
            # If calendar has no active settings either, we can skip full processing.
            if automation_level == 'off':
                cal_mode_check = calendar_automation_settings.get('mode', 'never')
                cal_accept_check = calendar_automation_settings.get('auto_accept_invites', 'never')
                cal_reschedule_check = calendar_automation_settings.get('reschedule_mode', 'never')
                cal_cancel_check = calendar_automation_settings.get('cancellation_mode', 'manual')
                cal_decline_check = calendar_automation_settings.get('auto_decline_mode', 'never')
                calendar_has_active_setting = any([
                    cal_mode_check != 'never',
                    cal_accept_check != 'never',
                    cal_reschedule_check != 'never',
                    cal_cancel_check != 'manual',
                    cal_decline_check != 'never'
                ])
                if not calendar_has_active_setting:
                    logger.info("⏭️ Automation level is 'off' and no active calendar settings — skipping processing")
                    if is_test_run and test_request_id:
                        try:
                            _now = datetime.now(timezone.utc).isoformat()
                            settings_table.update_item(
                                Key={'user_id': sender_username, 'storage_type': f'test#{test_request_id}'},
                                UpdateExpression='SET #data.#status = :s, #data.#result = :r, #data.processing.completed_at = :c, updated_at = :u',
                                ExpressionAttributeNames={'#data': 'data', '#status': 'status', '#result': 'result'},
                                ExpressionAttributeValues={':s': 'completed', ':r': {'action_taken': 'declined', 'confidence': confidence, 'reasoning': 'Automation is off and no active calendar settings', 'settings_applied': {'automation_level': automation_level}}, ':c': _now, ':u': _now}
                            )
                            logger.info("   ✅ Test record updated to completed (automation_off)")
                        except Exception as _te:
                            logger.warning("   ⚠️ Failed to update test record: %s", _te)
                    return {"result": {"skipped": True, "reason": "automation_off"}}
                else:
                    logger.info("📅 Automation level is 'off' but calendar automation is active — continuing for calendar only")

            # Determine if this email is eligible for auto-send
            should_auto_send = False
            auto_send_blocked_reason = None

            if automation_level == 'auto_send' and auto_send_rules.get('enabled', False):
                logger.info("   Automation level: auto_send (enabled)")

                # Rule 1: Confidence threshold
                confidence_threshold = auto_send_rules.get('confidence_threshold', 0.95)
                if confidence < confidence_threshold:
                    auto_send_blocked_reason = f"Confidence {confidence:.2f} below threshold {confidence_threshold}"
                    logger.info("   ❌ Auto-send blocked: %s", auto_send_blocked_reason)

                # Rule 2: Requires human review flag from analysis
                elif requires_review:
                    auto_send_blocked_reason = "Analysis flagged for human review"
                    logger.info("   ❌ Auto-send blocked: %s", auto_send_blocked_reason)

                # Rule 3: Intent requires review
                elif intent in auto_send_rules.get('require_review_for', []):
                    auto_send_blocked_reason = f"Intent '{intent}' requires review"
                    logger.info("   ❌ Auto-send blocked: %s", auto_send_blocked_reason)

                # Rule 4: Check excluded days (use user's timezone if set)
                elif auto_send_rules.get('excluded_days'):
                    from datetime import datetime
                    import pytz
                    user_tz_str = user_settings.get('timezone', 'America/Chicago')
                    try:
                        user_tz = pytz.timezone(user_tz_str)
                        today = datetime.now(user_tz).strftime('%A').lower()
                    except Exception:
                        today = datetime.utcnow().strftime('%A').lower()
                    if today in [d.lower() for d in auto_send_rules.get('excluded_days', [])]:
                        auto_send_blocked_reason = f"Today ({today}) is in excluded days"
                        logger.info("   ❌ Auto-send blocked: %s", auto_send_blocked_reason)

                # All checks passed!
                if not auto_send_blocked_reason:
                    should_auto_send = True
                    logger.info("   ✅ Auto-send approved! All rules passed.")

            elif automation_level == 'draft_only':
                logger.info("   Automation level: draft_only (auto-send disabled)")
            elif automation_level == 'off':
                logger.info("   Automation level: off (email response disabled, calendar-only mode)")
                auto_send_blocked_reason = "Automation is off"
            else:
                logger.info("   Automation level: %s", automation_level)

            # ===========================================
            # STEP 2.7: CALENDAR AUTOMATION DECISION
            # ===========================================
            # This determines how to handle the email based on intent + calendar_automation settings
            logger.info("📅 Step 2.7: Applying calendar automation rules...")

            calendar_automation = user_settings.get('calendar_automation', {})
            calendar_action = None  # Will be: None, 'create_event', 'accept', 'tentative', 'decline', 'offer_alternatives', 'reschedule', 'confirm_cancel'
            calendar_action_reason = None

            # Get all calendar automation settings with defaults
            cal_mode = calendar_automation.get('mode', 'never')  # never | confirmed_only | always
            auto_decline_mode = calendar_automation.get('auto_decline_mode', 'never')  # never | offer_alternatives | polite_decline
            auto_accept_invites = calendar_automation.get('auto_accept_invites', 'never')  # never | if_available | tentative_if_available | known_senders
            # Extract CC recipients for later use in calendar event attendees
            cc_recipients = common_headers.get('cc', [])
            if isinstance(cc_recipients, str):
                cc_recipients = [cc_recipients]
            reschedule_mode = calendar_automation.get('reschedule_mode', 'never')  # never | draft_only | auto_reschedule
            cancellation_mode = calendar_automation.get('cancellation_mode', 'manual')  # manual | auto_confirm | auto_decline

            logger.info("   Calendar settings: mode=%s, decline=%s, accept=%s, reschedule=%s, cancel=%s",
                       cal_mode, auto_decline_mode, auto_accept_invites, reschedule_mode, cancellation_mode)

            # Intent-based calendar automation logic
            if intent == 'decline':
                # Sender explicitly declined or user is unavailable for the requested time(s)
                logger.info("   📅 Intent: decline/unavailable - checking auto_decline_mode")
                if auto_decline_mode == 'offer_alternatives':
                    calendar_action = 'offer_alternatives'
                    calendar_action_reason = "Auto-offering alternative times (auto_decline_mode=offer_alternatives)"
                    logger.info("   ✅ Calendar action: offer_alternatives")
                elif auto_decline_mode == 'polite_decline':
                    calendar_action = 'polite_decline'
                    calendar_action_reason = "Auto-declining without alternatives (auto_decline_mode=polite_decline)"
                    logger.info("   ✅ Calendar action: polite_decline")
                else:
                    calendar_action = None
                    calendar_action_reason = "Decline requires manual review (auto_decline_mode=never)"
                    logger.info("   📝 Calendar action: manual review required")

            elif intent == 'calendar_invite':
                # Incoming calendar invite - check auto_accept_invites
                logger.info("   📅 Intent: calendar_invite - checking auto_accept_invites")

                # Check if user is available for the invite time
                invite_time_available = len(calendar_slots) > 0  # Simplified check

                if auto_accept_invites == 'if_available' and invite_time_available:
                    calendar_action = 'accept'
                    calendar_action_reason = "Auto-accepting invite (available + auto_accept_invites=if_available)"
                    logger.info("   ✅ Calendar action: accept")
                elif auto_accept_invites == 'tentative_if_available' and invite_time_available:
                    calendar_action = 'tentative'
                    calendar_action_reason = "Auto-tentative invite (available + auto_accept_invites=tentative_if_available)"
                    logger.info("   ✅ Calendar action: tentative")
                elif auto_accept_invites == 'known_senders':
                    # Known senders: check against user's VIP/priority senders list
                    vip_senders = user_settings.get('sender_filters', {}).get('vip', [])
                    sender_domain = source_email.split('@')[-1] if '@' in source_email else ''
                    is_known = any(
                        source_email.lower() == s.lower() or sender_domain.lower() == s.lower().lstrip('@')
                        for s in vip_senders
                    )
                    if is_known and invite_time_available:
                        calendar_action = 'accept'
                        calendar_action_reason = "Known (VIP) sender invite auto-accepted"
                        logger.info("   ✅ Calendar action: accept (VIP sender: %s)", source_email)
                    else:
                        calendar_action = None
                        reason = "not a known sender" if not is_known else "time unavailable"
                        calendar_action_reason = f"Known sender invite — manual review ({reason})"
                        logger.info("   📝 Calendar action: manual review (%s)", reason)
                else:
                    calendar_action = None
                    calendar_action_reason = "Invite requires manual review (auto_accept_invites=never)"
                    logger.info("   📝 Calendar action: manual review required")

            elif intent == 'reschedule':
                # Rescheduling request
                logger.info("   📅 Intent: reschedule - checking reschedule_mode")
                if reschedule_mode == 'auto_reschedule' and calendar_slots:
                    calendar_action = 'auto_reschedule'
                    calendar_action_reason = "Auto-rescheduling to available slot (reschedule_mode=auto_reschedule)"
                    logger.info("   ✅ Calendar action: auto_reschedule")
                elif reschedule_mode == 'draft_only':
                    calendar_action = 'draft_reschedule'
                    calendar_action_reason = "Drafting reschedule proposal (reschedule_mode=draft_only)"
                    logger.info("   ✅ Calendar action: draft_reschedule")
                else:
                    calendar_action = None
                    calendar_action_reason = "Reschedule requires manual review (reschedule_mode=never)"
                    logger.info("   📝 Calendar action: manual review required")

            elif intent == 'cancel':
                # Cancellation request
                logger.info("   📅 Intent: cancel - checking cancellation_mode")
                if cancellation_mode == 'auto_confirm':
                    calendar_action = 'confirm_cancel'
                    calendar_action_reason = "Auto-confirming cancellation (cancellation_mode=auto_confirm)"
                    logger.info("   ✅ Calendar action: confirm_cancel")
                elif cancellation_mode == 'auto_decline':
                    calendar_action = 'decline_cancel'
                    calendar_action_reason = "Auto-declining cancellation, proposing to keep meeting (cancellation_mode=auto_decline)"
                    logger.info("   ✅ Calendar action: decline_cancel")
                else:
                    calendar_action = None
                    calendar_action_reason = "Cancellation requires manual review (cancellation_mode=manual)"
                    logger.info("   📝 Calendar action: manual review required")

            elif intent in ['new_request', 'accept', 'confirm']:
                # New meeting request or acceptance - use cal_mode for event creation
                logger.info("   📅 Intent: %s - checking calendar mode", intent)
                if cal_mode == 'always':
                    calendar_action = 'create_tentative'
                    calendar_action_reason = "Creating tentative event (mode=always)"
                elif cal_mode == 'confirmed_only' and intent in ['accept', 'confirm']:
                    calendar_action = 'create_confirmed'
                    calendar_action_reason = "Creating confirmed event (mode=confirmed_only)"
                else:
                    calendar_action = None
                    calendar_action_reason = f"No auto calendar action for {intent} (mode={cal_mode})"
                logger.info("   📅 Calendar action: %s", calendar_action or 'none')

            # Store calendar decision for later use
            calendar_decision = {
                'action': calendar_action,
                'reason': calendar_action_reason,
                'settings_used': {
                    'mode': cal_mode,
                    'auto_decline_mode': auto_decline_mode,
                    'auto_accept_invites': auto_accept_invites,
                    'reschedule_mode': reschedule_mode,
                    'cancellation_mode': cancellation_mode
                }
            }
            logger.info("   📅 Calendar decision stored: %s", calendar_action or 'manual_review')

            # ===========================================
            # STEP 3: GENERATE DRAFT (HTTP API call)
            # ===========================================
            # If automation_level is 'off', skip email draft generation entirely.
            # We only reach here if calendar automation is active (Step 2.6 allowed us through).
            # Jump straight to calendar-only flow.
            if automation_level == 'off':
                logger.info("⏭️ Automation level is 'off' — skipping draft generation (Steps 3-5.5)")
                logger.info("   Proceeding directly to calendar automation (Step 5.75)...")

                # Still need IDs and timestamps for calendar tracking
                from datetime import datetime
                import uuid
                draft_id = None  # No draft in calendar-only mode
                request_id = str(uuid.uuid4())
                email_id = mail_data.get('messageId', request_id)
                timestamp = datetime.utcnow().isoformat() + 'Z'
                original_subject = common_headers.get('subject', 'Meeting Request')
                outlook_draft_id = None
                outlook_message_id = None
                action_taken = 'calendar_only'
                should_auto_send = False

                # Build AI decision for the scheduling request record
                ai_decision = {
                    'intent': intent,
                    'confidence': confidence,
                    'requires_review': requires_review,
                    'review_reason': analysis_data.get('review_reason'),
                    'action_taken': 'calendar_only',
                    'auto_send_eligible': False,
                    'auto_send_blocked_reason': 'Automation is off',
                    'meeting_details': meeting_details,
                    'calendar_slots_found': len(calendar_slots),
                    'proposed_times': [slot.get('start') for slot in calendar_slots[:5]] if calendar_slots else [],
                    'calendar_decision': calendar_decision
                }

                # Save scheduling request (no draft) to DynamoDB
                try:
                    storage_table = dynamodb.Table(os.environ.get('AI_SCHEDULER_STORAGE_TABLE'))
                    scheduling_request_item = {
                        'user_id': sender_username,
                        'storage_type': f'request#{request_id}',
                        'created_at': timestamp,
                        'updated_at': timestamp,
                        'data': {
                            'request_id': request_id,
                            'email_id': email_id,
                            'draft_id': None,
                            'calendar_event_id': None,
                            'status': 'pending_review',
                            'sub_status': 'calendar_only_mode',
                            'ai_decision': ai_decision,
                            'requester_email': reply_to_email,
                            'requester_name': reply_to_name,
                            'meeting': {
                                'subject': original_subject,
                                'purpose': meeting_details.get('purpose', 'Meeting request'),
                                'duration_minutes': meeting_details.get('duration_minutes', 30),
                                'location_preference': meeting_details.get('location_pref', 'Teams'),
                                'proposed_times': [slot.get('start') for slot in calendar_slots[:5]] if calendar_slots else [],
                                'confirmed_time': None,
                                'event_status': None,
                                'attendee_count': 1 + len(common_headers.get('cc', []))
                            },
                            'source': 'email_forwarding',
                            'auto_processed': True,
                            'scheduled_at': None,
                            'completed_at': None
                        }
                    }
                    storage_table.put_item(Item=convert_floats_to_decimal(scheduling_request_item))
                    logger.info("✅ Calendar-only scheduling request saved: request_id=%s", request_id)
                except Exception as e:
                    logger.error("❌ Failed to save calendar-only request: %s", e, exc_info=True)

                # Jump to Step 5.75 (calendar event handling) — skip draft steps entirely
                # We use a flag to skip the normal draft flow below
                _skip_draft_flow = True
            else:
                _skip_draft_flow = False

            if not _skip_draft_flow:
                logger.info("📝 Step 3: Generating draft response...")

                try:
                    from agent.prompt import create_llm, Prompt
                    from pycommon.api.models import get_default_models as _get_models_draft

                    _draft_models = _get_models_draft(access_token)
                    _draft_model = _draft_models.get('cheapest_model') or _draft_models.get('agent_model')

                    if not _draft_model:
                        logger.error("❌ Could not fetch model for draft generation")
                        return {"result": None, "error": "model_fetch_failed"}

                    _draft_llm = create_llm(
                        access_token,
                        _draft_model,
                        sender_username,
                        {'account_id': 'general_account', 'api_key_id': api_key_id, 'rate_limit': None},
                        {'purpose': 'draft_generation'}
                    )

                    executive_name = user_settings.get('name') or sender_username

                    # Build system prompt
                    _draft_system = (
                        f"You are an executive assistant helping {executive_name} draft professional email responses. "
                        "Generate a warm, professional, and concise reply that:\n"
                        "1. Acknowledges the sender's email\n"
                        "2. Addresses their request or question directly\n"
                        "3. Uses an appropriate tone based on the email context\n\n"
                        "CRITICAL FORMATTING RULES:\n"
                        "- Do NOT include ANY closing or sign-off (no 'Best,', 'Regards,', 'Thanks,', 'Sincerely,', etc.)\n"
                        "- Do NOT include the sender's name at the end\n"
                        "- End the email body with the last sentence of content. The signature will be added automatically.\n"
                        "- Return ONLY the email body text, no JSON or extra formatting"
                    )

                    # Build times context from calendar_slots
                    _times_context = ""
                    if calendar_slots:
                        import datetime as _dt
                        _slots_formatted = []
                        for slot in calendar_slots[:5]:
                            try:
                                _start = slot.get('start', '')
                                _slots_formatted.append(f"  - {_start}")
                            except Exception:
                                pass
                        if _slots_formatted:
                            _times_context = "\n\nAVAILABLE TIMES TO PROPOSE:\n" + "\n".join(_slots_formatted) + "\nIMPORTANT: Propose these specific times and ask which works best."

                    # Add calendar action context
                    _action_context = ""
                    if calendar_action and calendar_action != 'manual_review':
                        _action_context = f"\n\nCALENDAR ACTION: {calendar_action}. Reason: {calendar_action_reason or 'N/A'}"

                    _draft_user = f"""Generate a professional email response for the following:

FROM: {reply_to_email} ({reply_to_name})
SUBJECT: {common_headers.get('subject', '')}

EMAIL BODY:
{email_body[:2000]}
{_times_context}{_action_context}

Write a natural, warm, professional response that {executive_name} would send."""

                    _draft_prompt = Prompt(messages=[
                        {'role': 'system', 'content': _draft_system},
                        {'role': 'user', 'content': _draft_user}
                    ])

                    draft_text = _draft_llm(_draft_prompt)

                    if not draft_text:
                        logger.error("❌ LLM returned empty draft")
                        return {"result": None, "error": "draft_generation_failed"}

                    draft_text = draft_text.strip()

                    # Track what content type to send to Outlook.
                    # Plain text by default; switches to 'html' when the signature is HTML.
                    draft_content_type = 'text'

                    # Append signature if available
                    if primary_signature and primary_signature.get('content'):
                        sig_content = primary_signature['content']
                        sig_format = primary_signature.get('format', 'text')

                        if sig_format == 'html':
                            # HTML signature — convert the entire draft to HTML so the
                            # signature renders properly in Outlook and in our pane.
                            # Wrap the message in a div and the signature in a sentinel div
                            # (<div id="amplify-signature">) so the frontend can cleanly
                            # split them: editable message body on top, read-only rendered
                            # signature preview below.
                            import html as _html_lib

                            def _text_to_html_paragraphs(text):
                                """Convert plain LLM text to HTML paragraphs."""
                                paragraphs = text.strip().split('\n\n')
                                parts = []
                                for para in paragraphs:
                                    escaped = _html_lib.escape(para).replace('\n', '<br>\n')
                                    parts.append(f'<p>{escaped}</p>')
                                return '\n'.join(parts)

                            message_html = _text_to_html_paragraphs(draft_text)
                            draft_text = (
                                f'<div class="amplify-draft-message">\n{message_html}\n</div>\n'
                                f'<div id="amplify-signature">\n{sig_content}\n</div>'
                            )
                            draft_content_type = 'html'
                            logger.info("   ✅ Appended HTML signature (contentType=html): %s", primary_signature.get('name'))
                        else:
                            # Plain text signature — simple concatenation, stays as text
                            if not draft_text.endswith('\n\n'):
                                draft_text += '\n\n'
                            draft_text += sig_content
                            logger.info("   ✅ Appended plain text signature: %s", primary_signature.get('name'))
                    else:
                        logger.info("   ℹ️ No signature to append")

                    logger.info("✅ Draft generated: %d chars (content_type=%s)", len(draft_text), draft_content_type)

                except Exception as e:
                    logger.error("❌ Failed to generate draft: %s", e, exc_info=True)
                    return {"result": None, "error": "api_call_failed"}

            if not _skip_draft_flow:
                # ===========================================
                # STEP 4: SAVE DRAFT TO DYNAMODB (user#draft#{id} pattern)
                # ===========================================
                logger.info("💾 Step 4: Saving draft to DynamoDB for frontend access...")

                # Generate IDs upfront for bidirectional linking
                from datetime import datetime
                import uuid

                draft_id = str(uuid.uuid4())
                request_id = str(uuid.uuid4())  # Generate request_id NOW so draft can reference it
                email_id = mail_data.get('messageId', request_id)
                timestamp = datetime.utcnow().isoformat() + 'Z'

                # Build comprehensive AI decision object for tracking
                ai_decision = {
                    'intent': intent,
                    'confidence': confidence,
                    'requires_review': requires_review,
                    'review_reason': analysis_data.get('review_reason'),
                    'action_taken': None,  # Will be updated later: draft_created | auto_sent | declined
                    'auto_send_eligible': should_auto_send,
                    'auto_send_blocked_reason': auto_send_blocked_reason,
                    'meeting_details': meeting_details,
                    'calendar_slots_found': len(calendar_slots),
                    'proposed_times': [slot.get('start') for slot in calendar_slots[:5]] if calendar_slots else [],
                    # Calendar automation decision
                    'calendar_decision': calendar_decision
                }

                logger.info("   🤖 AI Decision: intent=%s, confidence=%.2f, requires_review=%s, calendar_action=%s",
                           intent, confidence, requires_review, calendar_action or 'none')

                # Construct draft subject
                original_subject = common_headers.get('subject', 'Meeting Request')
                draft_subject = f"Re: {original_subject}" if not original_subject.startswith('Re:') else original_subject

                try:
                    settings_table = dynamodb.Table(os.environ.get('AI_SCHEDULER_STORAGE_TABLE'))

                    # Store draft with user#draft#{id} pattern for extensibility
                    draft_item = {
                        'user_id': sender_username,
                        'storage_type': f"draft#{draft_id}",
                        'created_at': timestamp,
                        'updated_at': timestamp,
                        'data': {
                            # ====== BIDIRECTIONAL LINKING ======
                            'draft_id': draft_id,
                            'request_id': request_id,
                            'email_id': email_id,

                            # Draft content
                            'subject': draft_subject,
                            'body': draft_text,
                            'to_recipients': [reply_to_email],
                            'cc_recipients': [],
                            'bcc_recipients': [],
                            'importance': 'normal',

                            # Source tracking
                            'source': 'email_forwarding',
                            'source_email_id': email_id,
                            'source_email_subject': original_subject,
                            'source_email_from': reply_to_email,
                            'source_email_from_name': reply_to_name,

                            # ====== LIFECYCLE STATUS ======
                            'status': 'pending',
                            'outlook_draft_id': None,
                            'outlook_message_id': None,

                            # ====== AI DECISION (subset for quick access) ======
                            'intent': intent,
                            'confidence': str(confidence),
                            'requires_review': requires_review,
                            'auto_send_approved': should_auto_send,

                            # ====== CALENDAR EVENT TRACKING ======
                            'calendar_event_id': None,
                            'calendar_event_status': None,

                            # ====== TIMESTAMPS ======
                            'sent_at': None
                        }
                    }

                    settings_table.put_item(Item=draft_item)
                    logger.info("✅ Draft saved to DynamoDB: draft_id=%s, request_id=%s", draft_id, request_id)
                    logger.info("   ↔️ Bidirectional link: draft#%s ←→ request#%s", draft_id[:8], request_id[:8])

                except Exception as e:
                    logger.error("❌ Failed to save draft to DynamoDB: %s", e, exc_info=True)
                    draft_id = f"draft_{sender_username}_{int(__import__('time').time())}"

                # ===========================================
                # STEP 5: SAVE SCHEDULING REQUEST TO DYNAMODB
                # ===========================================
                logger.info("💽 Step 5: Saving scheduling request with full lifecycle tracking...")

                try:
                    storage_table = dynamodb.Table(os.environ.get('AI_SCHEDULER_STORAGE_TABLE'))

                    storage_type = f'request#{request_id}'

                    if requires_review:
                        lifecycle_status = 'pending_review'
                    elif should_auto_send:
                        lifecycle_status = 'auto_processing'
                    else:
                        lifecycle_status = 'pending_review'

                    scheduling_request_item = {
                        'user_id': sender_username,
                        'storage_type': storage_type,
                        'created_at': timestamp,
                        'updated_at': timestamp,
                        'data': {
                            'request_id': request_id,
                            'email_id': email_id,
                            'draft_id': draft_id,
                            'calendar_event_id': None,
                            'status': lifecycle_status,
                            'sub_status': None,
                            'ai_decision': ai_decision,
                            'requester_email': reply_to_email,
                            'requester_name': reply_to_name,
                            'meeting': {
                                'subject': original_subject,
                                'purpose': meeting_details.get('purpose', 'Meeting request'),
                                'duration_minutes': meeting_details.get('duration_minutes', 30),
                                'location_preference': meeting_details.get('location_pref', 'Teams'),
                                'proposed_times': [slot.get('start') for slot in calendar_slots[:5]] if calendar_slots else [],
                                'confirmed_time': None,
                                'event_status': None,
                                'attendee_count': 1 + len(common_headers.get('cc', []))
                            },
                            'source': 'email_forwarding',
                            'auto_processed': True,
                            'scheduled_at': None,
                            'completed_at': None
                        }
                    }

                    storage_table.put_item(Item=convert_floats_to_decimal(scheduling_request_item))
                    logger.info("✅ Scheduling request saved: request_id=%s", request_id)
                    logger.info("   ↔️ Bidirectional link: request#%s ←→ draft#%s", request_id[:8], draft_id[:8])
                    logger.info("   📊 Status: %s", lifecycle_status)

                except Exception as e:
                    logger.error("❌ Failed to save to DynamoDB storage table: %s", e, exc_info=True)

                # ===========================================
                # STEP 5.5: TAKE ACTION (Create Outlook Draft OR Send Email)
                # ===========================================
                logger.info("🚀 Step 5.5: Taking action based on automation level...")

                outlook_draft_id = None
                outlook_message_id = None
                action_taken = 'none'

                try:
                    if should_auto_send:
                        # AUTO-SEND: Send email directly via Outlook
                        logger.info("   📧 AUTO-SEND mode: Sending email directly...")

                        send_response = requests.post(
                            f'{amp_base_url}/microsoft/integrations/send_mail',
                            headers={
                                'Authorization': f'Bearer {access_token}',
                                'Content-Type': 'application/json'
                            },
                            json={
                                'data': {
                                    'user_id': sender_username,
                                    'to_recipients': [reply_to_email],
                                    'subject': draft_subject,
                                    'body': draft_text,
                                    'importance': 'normal',
                                    'save_to_sent_items': True
                                }
                            },
                            timeout=30
                        )

                        if send_response.status_code == 200:
                            send_result = send_response.json()
                            if send_result.get('success'):
                                outlook_message_id = send_result['data'].get('message_id')
                                action_taken = 'auto_sent'
                                ai_decision['action_taken'] = 'auto_sent'
                                sent_timestamp = datetime.now(timezone.utc).isoformat()
                                logger.info("   ✅ Email sent automatically! Message ID: %s", outlook_message_id)

                                settings_table.update_item(
                                    Key={'user_id': sender_username, 'storage_type': f"draft#{draft_id}"},
                                    UpdateExpression='SET #data.#status = :status, #data.outlook_message_id = :msg_id, #data.sent_at = :sent_at, updated_at = :updated',
                                    ExpressionAttributeNames={'#data': 'data', '#status': 'status'},
                                    ExpressionAttributeValues={':status': 'sent', ':msg_id': outlook_message_id, ':sent_at': sent_timestamp, ':updated': sent_timestamp}
                                )
                                storage_table.update_item(
                                    Key={'user_id': sender_username, 'storage_type': f"request#{request_id}"},
                                    UpdateExpression='SET #data.#status = :status, #data.ai_decision = :ai_dec, updated_at = :updated',
                                    ExpressionAttributeNames={'#data': 'data', '#status': 'status'},
                                    ExpressionAttributeValues={':status': 'auto_handled', ':ai_dec': ai_decision, ':updated': sent_timestamp}
                                )
                                logger.info("   📊 Request status updated to: auto_handled")
                            else:
                                logger.error("   ❌ Send email failed: %s", send_result.get('message'))
                                should_auto_send = False
                        else:
                            logger.error("   ❌ Send email API returned %d", send_response.status_code)
                            should_auto_send = False

                    # If NOT auto-sending, create Outlook draft
                    if not should_auto_send:
                        logger.info("   📝 Creating Outlook draft for user review...")
                        try:
                            # Call scheduler backend to create Outlook draft (proxies to Microsoft backend)
                            draft_create_response = requests.post(
                                f'{api_base_url}/scheduling/drafts/create',
                                headers={
                                    'Authorization': f'Bearer {access_token}',
                                    'Content-Type': 'application/json'
                                },
                                json={
                                    'data': {
                                        'user_id': sender_username,
                                        'to_recipients': [reply_to_email],
                                        'subject': draft_subject,
                                        'body': draft_text,
                                        'importance': 'normal',
                                        'content_type': draft_content_type
                                    }
                                },
                                timeout=30
                            )
                            if draft_create_response.status_code == 200:
                                draft_create_result = draft_create_response.json()
                                if draft_create_result.get('success'):
                                    outlook_draft_id = draft_create_result.get('data', {}).get('message_id') or draft_create_result.get('data', {}).get('id')
                                else:
                                    raise ValueError(f"Draft creation failed: {draft_create_result.get('message', 'unknown error')}")
                            else:
                                raise ValueError(f"Draft creation returned {draft_create_response.status_code}: {draft_create_response.text[:200]}")
                            action_taken = 'draft_created'
                            ai_decision['action_taken'] = 'draft_created'
                            draft_timestamp = datetime.now(timezone.utc).isoformat()
                            logger.info("   ✅ Outlook draft created! Draft ID: %s", outlook_draft_id)

                            settings_table.update_item(
                                Key={'user_id': sender_username, 'storage_type': f"draft#{draft_id}"},
                                UpdateExpression='SET #data.outlook_draft_id = :outlook_id, #data.#status = :status, updated_at = :updated',
                                ExpressionAttributeNames={'#data': 'data', '#status': 'status'},
                                ExpressionAttributeValues={':outlook_id': outlook_draft_id, ':status': 'outlook_draft_created', ':updated': draft_timestamp}
                            )
                            storage_table.update_item(
                                Key={'user_id': sender_username, 'storage_type': f"request#{request_id}"},
                                UpdateExpression='SET #data.ai_decision = :ai_dec, updated_at = :updated',
                                ExpressionAttributeNames={'#data': 'data'},
                                ExpressionAttributeValues={':ai_dec': ai_decision, ':updated': draft_timestamp}
                            )
                            logger.info("   📊 Request status: pending_review (draft created for user)")
                        except Exception as draft_err:
                            logger.error("   ❌ Inline draft creation failed: %s", draft_err)

                except Exception as e:
                    logger.error("❌ Failed to create Outlook draft/send email: %s", e, exc_info=True)
                    logger.info("   Draft still saved to DynamoDB for manual processing")

                # ===========================================
                # STEP 5.6: NOTIFICATION DISPATCH
                # ===========================================
                notification_prefs = user_settings.get('notification_preferences', {})
                try:
                    should_notify = False
                    notification_subject = None
                    notification_body = None

                    if action_taken == 'draft_created' and notification_prefs.get('email_on_draft'):
                        should_notify = True
                        notification_subject = f'📝 AI Scheduler: Draft created for "{original_subject}"'
                        notification_body = (
                            f'A draft response has been created for an email from {sender_name} <{source_email}>.\n\n'
                            f'Subject: {original_subject}\n'
                            f'Intent: {intent} (confidence: {confidence:.0%})\n'
                            f'Action: Draft created for your review\n\n'
                            f'Open the AI Scheduler app to review and send this draft.'
                        )
                    elif action_taken == 'auto_sent' and notification_prefs.get('email_on_auto_send'):
                        should_notify = True
                        notification_subject = f'📧 AI Scheduler: Response auto-sent for "{original_subject}"'
                        notification_body = (
                            f'An email response was automatically sent to {sender_name} <{source_email}>.\n\n'
                            f'Subject: {original_subject}\n'
                            f'Intent: {intent} (confidence: {confidence:.0%})\n'
                            f'Action: Auto-sent\n\n'
                            f'The response was sent because your automation level is set to auto_send '
                            f'and all safety rules passed.\n\n'
                            f'Open the AI Scheduler app to review the sent message.'
                        )

                    if should_notify:
                        logger.info("🔔 Step 5.6: Sending notification email to user...")
                        notify_response = requests.post(
                            f'{amp_base_url}/microsoft/integrations/send_mail',
                            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
                            json={'data': {'user_id': sender_username, 'to_recipients': [source_email], 'subject': notification_subject, 'body': notification_body, 'importance': 'normal', 'save_to_sent_items': False}},
                            timeout=15
                        )
                        if notify_response.status_code == 200 and notify_response.json().get('success'):
                            logger.info("   ✅ Notification email sent")
                        else:
                            logger.warning("   ⚠️ Notification email failed (non-critical)")
                    else:
                        logger.info("🔕 Step 5.6: No notification required (action=%s)", action_taken)

                except Exception as e:
                    logger.warning("⚠️ Notification dispatch failed (non-critical): %s", e)

            # ===========================================
            # STEP 5.75: CALENDAR EVENT CREATION (uses calendar_decision from Step 2.7)
            # ===========================================
            logger.info("📅 Step 5.75: Calendar event handling based on calendar_decision...")

            calendar_automation = user_settings.get('calendar_automation', {})
            event_created = False
            calendar_event_id = None

            try:
                # Use calendar_decision from Step 2.7 instead of re-checking settings
                should_create_event = calendar_action in ['create_tentative', 'create_confirmed', 'accept', 'auto_reschedule']
                should_delete_event = calendar_action in ['confirm_cancel', 'auto_reschedule']

                # Determine event status based on calendar_action
                # Determine event_status:
                # For confirmed/accepted/rescheduled events, respect the user's 'Mark as Busy' setting.
                # For tentative proposals (create_tentative), also respect the setting — user explicitly chose.
                user_event_status = calendar_automation.get('event_status', 'tentative')
                if calendar_action in ['create_confirmed', 'accept', 'create_tentative', 'auto_reschedule']:
                    event_status_type = user_event_status
                elif calendar_action == 'tentative':
                    event_status_type = 'tentative'  # tentative_if_available always creates tentative
                else:
                    event_status_type = 'tentative'

                logger.info("   📅 Calendar action: %s, should_create_event: %s, status: %s",
                           calendar_action or 'none', should_create_event, event_status_type)

                # ===========================================
                # P2/P3: DELETE OR CANCEL EXISTING CALENDAR EVENT
                # For auto_reschedule: cancel old event before creating new one
                # For confirm_cancel: delete the existing calendar event
                # ===========================================
                if should_delete_event:
                    # Look up existing calendar_event_id from the scheduling request
                    existing_event_id = None
                    try:
                        # Query for existing requests from this sender about the same subject
                        existing_requests = storage_table.query(
                            KeyConditionExpression='user_id = :uid AND begins_with(storage_type, :prefix)',
                            ExpressionAttributeValues={
                                ':uid': sender_username,
                                ':prefix': 'request#'
                            },
                            ScanIndexForward=False,
                            Limit=20
                        )
                        for req_item in existing_requests.get('Items', []):
                            req_meeting = req_item.get('meeting', {})
                            if (req_item.get('calendar_event_id') and
                                req_item.get('requester_email') == reply_to_email and
                                req_meeting.get('subject', '').lower() in common_headers.get('subject', '').lower()):
                                existing_event_id = req_item['calendar_event_id']
                                logger.info("   🔍 Found existing calendar event: %s", existing_event_id)
                                break
                    except Exception as e:
                        logger.warning("   ⚠️ Could not look up existing event: %s", e)

                    if existing_event_id:
                        try:
                            logger.info("   🗑️ Deleting existing calendar event: %s", existing_event_id)
                            delete_response = requests.post(
                                f'{api_base_url}/scheduling/event/cancel',
                                headers={
                                    'Authorization': f'Bearer {access_token}',
                                    'Content-Type': 'application/json'
                                },
                                json={
                                    'data': {
                                        'user_id': sender_username,
                                        'event_id': existing_event_id
                                    }
                                },
                                timeout=15
                            )
                            if delete_response.status_code == 200 and delete_response.json().get('success'):
                                logger.info("   ✅ Existing calendar event deleted")
                                # If this was a confirm_cancel, update the old request status
                                if calendar_action == 'confirm_cancel':
                                    event_timestamp = datetime.now(timezone.utc).isoformat()
                                    storage_table.update_item(
                                        Key={'user_id': sender_username, 'storage_type': f"request#{request_id}"},
                                        UpdateExpression='SET #data.#status = :status, #data.sub_status = :sub, updated_at = :updated',
                                        ExpressionAttributeNames={'#data': 'data', '#status': 'status'},
                                        ExpressionAttributeValues={':status': 'cancelled', ':sub': 'calendar_event_deleted', ':updated': event_timestamp}
                                    )
                            else:
                                logger.warning("   ⚠️ Could not delete existing event (continuing anyway)")
                        except Exception as e:
                            logger.warning("   ⚠️ Event deletion failed (non-critical): %s", e)
                    else:
                        logger.info("   📝 No existing calendar event found to delete")

                if should_create_event and calendar_slots:
                    # Extract meeting details from calendar slots or AI analysis
                    # For now, use first available slot
                    first_slot = calendar_slots[0]

                    # Build attendees list: always include original sender;
                    # include CC'd recipients only if include_ccd_recipients is enabled
                    attendees = [source_email]
                    if calendar_automation.get('include_ccd_recipients') and cc_recipients:
                        attendees.extend([
                            addr.strip() for addr in cc_recipients
                            if addr.strip() and addr.strip() != source_email
                        ])

                    # Create calendar event via Microsoft Graph
                    logger.info("   📅 Creating calendar event via Graph API...")

                    event_response = requests.post(
                        f'{amp_base_url}/microsoft/integrations/create_event',
                        headers={
                            'Authorization': f'Bearer {access_token}',
                            'Content-Type': 'application/json'
                        },
                        json={
                            'data': {
                                'user_id': sender_username,
                                'subject': common_headers.get('subject', 'Meeting Request'),
                                'body': f'Meeting scheduled via AI Scheduler\n\nOriginal request from: {sender_name} <{source_email}>',
                                'start': first_slot.get('start'),
                                'end': first_slot.get('end'),
                                'show_as': event_status_type,  # 'busy' or 'tentative'
                                'attendees': attendees,
                                'location': '',
                                'timezone': user_settings.get('timezone', 'America/Chicago')
                            }
                        },
                        timeout=30
                    )

                    if event_response.status_code == 200:
                        event_result = event_response.json()
                        if event_result.get('success'):
                            calendar_event_id = event_result['data'].get('event_id')
                            event_created = True
                            logger.info("   ✅ Calendar event created! Event ID: %s", calendar_event_id)
                            logger.info("      Status: %s", event_status_type)

                            event_timestamp = datetime.now(timezone.utc).isoformat()

                            # Update draft record with calendar event ID
                            settings_table.update_item(
                                Key={
                                    'user_id': sender_username,
                                    'storage_type': f"draft#{draft_id}"
                                },
                                UpdateExpression='SET #data.calendar_event_id = :event_id, #data.calendar_event_status = :event_status, updated_at = :updated',
                                ExpressionAttributeNames={'#data': 'data'},
                                ExpressionAttributeValues={
                                    ':event_id': calendar_event_id,
                                    ':event_status': event_status_type,
                                    ':updated': event_timestamp
                                }
                            )

                            # Update scheduling request record with calendar event and scheduled status
                            new_status = 'scheduled' if event_status_type == 'busy' else 'tentative_hold'
                            storage_table.update_item(
                                Key={
                                    'user_id': sender_username,
                                    'storage_type': f"request#{request_id}"
                                },
                                UpdateExpression='SET #data.calendar_event_id = :event_id, #data.#status = :status, #data.sub_status = :sub, #data.meeting.confirmed_time = :time, #data.meeting.event_status = :event_status, #data.scheduled_at = :sched, updated_at = :updated',
                                ExpressionAttributeNames={'#data': 'data', '#status': 'status'},
                                ExpressionAttributeValues={
                                    ':event_id': calendar_event_id,
                                    ':status': new_status,
                                    ':sub': 'calendar_event_created',
                                    ':time': first_slot.get('start'),
                                    ':event_status': event_status_type,
                                    ':sched': event_timestamp,
                                    ':updated': event_timestamp
                                }
                            )
                            logger.info("   📊 Request status updated to: %s (calendar event created)", new_status)
                        else:
                            logger.error("   ❌ Calendar event creation failed: %s", event_result.get('message'))
                    else:
                        logger.error("   ❌ Calendar event API returned %d", event_response.status_code)
                else:
                    logger.info("   ⏭️  Skipping calendar event creation")
                    logger.info("      Mode: %s, Intent: %s", cal_mode, intent)

            except Exception as e:
                logger.error("❌ Failed to create calendar event: %s", e, exc_info=True)

            # ===========================================
            # STEP 6: SUMMARY AND FRONTEND ACCESS
            # ===========================================
            logger.info("📦 Step 6: Action complete, summary...")
            logger.info("   👉 Frontend should:")
            logger.info("      1. Query AI_SCHEDULER_STORAGE_TABLE with:")
            logger.info("         - user_id = %s", sender_username)
            logger.info("         - storage_type begins_with 'draft#'")
            logger.info("      2. Display drafts in Drafts section")
            logger.info("      3. When user clicks 'Create in Outlook':")
            logger.info("         - Call Microsoft Graph API create_draft()")
            logger.info("         - Update draft item with outlook_draft_id")
            logger.info("      4. When user sends from Outlook:")
            logger.info("         - Update status = 'sent'")
            logger.info("         - Update sent_at timestamp")

            # Log final status
            if action_taken == 'auto_sent':
                logger.info("✅ EMAIL SENT AUTOMATICALLY!")
                logger.info("   Message ID: %s", outlook_message_id)
            elif action_taken == 'draft_created':
                logger.info("📝 OUTLOOK DRAFT CREATED")
                logger.info("   Draft ID: %s", outlook_draft_id)
                logger.info("   User can review and send from Outlook")
            else:
                logger.info("📋 DRAFT SAVED TO DYNAMODB")
                logger.info("   Frontend can display for user review")

            if requires_review:
                logger.info("⚠️ This draft requires human review")
            if auto_send_blocked_reason:
                logger.info("🚫 Auto-send blocked: %s", auto_send_blocked_reason)

            # ===========================================
            # COMPLETE
            # ===========================================
            logger.info("=" * 60)
            logger.info("✅ SES SCHEDULING FLOW COMPLETE")
            logger.info("=" * 60)
            logger.info("📊 SUMMARY:")
            logger.info("   User: %s", sender_username)
            logger.info("   From: %s <%s>", sender_name, source_email)
            logger.info("   Intent: %s", intent)
            logger.info("   Confidence: %.2f", confidence)
            logger.info("   Automation Level: %s", automation_level)
            logger.info("   Action Taken: %s", action_taken)
            logger.info("   Calendar Slots Checked: %d", len(calendar_slots))
            logger.info("   Requires Review: %s", requires_review)
            logger.info("=" * 60)

            # Update test record to completed if this was a test run
            _final_result = {
                "request_id": request_id,
                "draft_id": draft_id,
                "intent": intent,
                "confidence": confidence,
                "action_taken": action_taken,
                "draft_created": True,
                "auto_sent": action_taken == 'auto_sent',
                "outlook_draft_created": action_taken == 'draft_created',
                "outlook_draft_id": outlook_draft_id,
                "outlook_message_id": outlook_message_id,
                "requires_review": requires_review,
                "auto_send_blocked_reason": auto_send_blocked_reason,
                "calendar_slots_found": len(calendar_slots),
                "settings_applied": {
                    "automation_level": automation_level,
                    "calendar_automation": user_settings.get('calendar_automation', {}),
                    "auto_send_rules": user_settings.get('auto_send_rules', {})
                }
            }
            if is_test_run and test_request_id:
                try:
                    _now = datetime.now(timezone.utc).isoformat()
                    settings_table.update_item(
                        Key={'user_id': sender_username, 'storage_type': f'test#{test_request_id}'},
                        UpdateExpression='SET #data.#status = :s, #data.#result = :r, #data.processing.completed_at = :c, updated_at = :u',
                        ExpressionAttributeNames={'#data': 'data', '#status': 'status', '#result': 'result'},
                        ExpressionAttributeValues={':s': 'completed', ':r': convert_floats_to_decimal(_final_result), ':c': _now, ':u': _now}
                    )
                    logger.info("   ✅ Test record updated to completed (action_taken=%s)", action_taken)
                except Exception as _te:
                    logger.warning("   ⚠️ Failed to update test record: %s", _te)

            # return must contain result
            return {
                "result": _final_result
            }

        except Exception as e:
            logger.error("Error processing scheduling email: %s", e, exc_info=True)
            raise

    def onFailure(self, event: Dict[str, Any], error: Exception) -> None:
        logger.error("SESSchedulingMessageHandler onFailure: %s", error)
        pass

    def onSuccess(
        self, agent_input_event: Dict[str, Any], agent_result: Dict[str, Any]
    ) -> None:
        """Handle successful scheduling event processing"""
        logger.info("Scheduling email processed successfully")

    