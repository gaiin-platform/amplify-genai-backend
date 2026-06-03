
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
            logger.info("   API base URL (amplify): %s", amp_base_url)
            logger.info("   API base URL (scheduling): %s", api_base_url)
            logger.info("   Email body: plain=%d chars, html=%d chars",
                       len(parsed_email.get('body_plain') or ''), len(email_body_html or ''))

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
            # buried in the chain. Priority order:
            #   1. Regex on structured From:/Cc: headers (covers addin format + native Outlook)
            #   2. LLM extraction — last resort only, receives clean stripped text (no raw HTML)
            logger.info("📨 Step 0.7: Extracting true reply-to sender...")

            reply_to_email = source_email  # default: reply to whoever forwarded
            reply_to_name = sender_name    # default: their display name
            _forwarded_cc = []            # CC recipients parsed from the forwarded header block

            # Build a clean plain-text search body — fall back to stripped HTML when plain is short
            _body_for_search = email_body or ''
            if len(_body_for_search) < 500 and email_body_html:
                _body_for_search = _html_to_plain_text(email_body_html)

            _forward_markers = [
                'forwarded message', 'begin forwarded message',
                'original message', '-----forwarded', 'fwd:', 'fw:'
            ]
            _body_lower = _body_for_search.lower()
            _subject_lower = common_headers.get('subject', '').lower()
            _looks_forwarded = (
                any(m in _body_lower for m in _forward_markers) or
                _subject_lower.startswith('fwd:') or
                _subject_lower.startswith('fw:')
            )

            if _looks_forwarded:
                logger.info("   📧 Forwarded email detected — attempting sender extraction")
                import re as _re

                # --- Attempt 1: Regex on structured From: headers (addin block + native Outlook) ---
                # Scans the full body for ALL "From: Name <email>" lines, takes the deepest
                # (last) one that isn't the person who forwarded (source_email).
                try:
                    _from_re = _re.compile(
                        r'^From:\s*([^<\n]+?)\s*<([^>\n@]+@[^>\n@]+)>',
                        _re.IGNORECASE | _re.MULTILINE
                    )
                    _from_matches = _from_re.findall(_body_for_search)
                    for _f_name, _f_email in reversed(_from_matches):
                        _f_email_clean = _f_email.strip().lower()
                        if _f_email_clean != source_email.lower():
                            reply_to_email = _f_email_clean
                            reply_to_name = _f_name.strip() or _f_email_clean.split('@')[0]
                            logger.info("   ✅ Regex extracted reply-to: %s (%s)", reply_to_email, reply_to_name)
                            break

                    # Also parse Cc: line from the structured header block
                    if reply_to_email != source_email:
                        _cc_match = _re.search(r'^Cc:\s*(.+)$', _body_for_search, _re.IGNORECASE | _re.MULTILINE)
                        if _cc_match:
                            _forwarded_cc = [
                                m.strip().lower()
                                for m in _re.findall(r'<([^>\n@]+@[^>\n@]+)>', _cc_match.group(1))
                            ]
                            if _forwarded_cc:
                                logger.info("   📋 Extracted %d CC(s) from forward header: %s", len(_forwarded_cc), _forwarded_cc)

                except Exception as _regex_err:
                    logger.warning("   ⚠️ Regex sender extraction failed: %s", _regex_err)

                # --- Attempt 2: LLM — last resort only, uses clean stripped text ---
                if reply_to_email == source_email:
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
                                            'You are an email routing assistant. Given a forwarded email chain, '
                                            'identify the ORIGINAL sender — the person who wrote the scheduling '
                                            'request (not whoever forwarded it). Respond ONLY with valid JSON: '
                                            '{"reply_to_email": "email@example.com", "reply_to_name": "First Last"} '
                                            'or {"reply_to_email": null, "reply_to_name": null} if unknown.'
                                        )
                                    },
                                    {
                                        'role': 'user',
                                        'content': _body_for_search[:3000]  # clean text, no raw HTML
                                    }
                                ]
                            )
                            _llm_raw = _extract_llm(_extract_prompt)
                            _json_match = _re.search(r'\{[^{}]+\}', _llm_raw or '')
                            if _json_match:
                                _parsed = json.loads(_json_match.group())
                                _llm_email = _parsed.get('reply_to_email')
                                _llm_name = _parsed.get('reply_to_name')
                                if _llm_email and '@' in str(_llm_email) and str(_llm_email).lower() not in ('null', 'none', ''):
                                    reply_to_email = str(_llm_email).strip().lower()
                                    _name_raw = str(_llm_name) if _llm_name else None
                                    reply_to_name = (
                                        _name_raw.strip()
                                        if _name_raw and _name_raw.lower() not in ('null', 'none')
                                        else reply_to_email.split('@')[0]
                                    )
                                    logger.info("   ✅ LLM extracted reply-to: %s (%s)", reply_to_email, reply_to_name)
                                else:
                                    logger.info("   ℹ️ LLM returned null reply_to — keeping sender as reply-to")
                        else:
                            logger.warning("   ⚠️ No agent_model configured — skipping LLM extraction")

                    except Exception as _llm_err:
                        logger.warning("   ⚠️ LLM sender extraction failed: %s", _llm_err)
            else:
                logger.info("   ℹ️ Not a forwarded email — reply-to = envelope sender: %s", source_email)

            logger.info("   📨 Final reply-to: %s (%s)", reply_to_email, reply_to_name)

            # ===========================================
            # STEP 0.75: SEARCH FOR ORIGINAL EMAIL THREAD
            # ===========================================
            # Run for BOTH forwarded emails AND direct Re: replies so that
            # Outlook drafts/replies are placed in the existing conversation thread
            # instead of going out as a brand-new standalone email.
            original_message_id = None
            original_conversation_id = None

            import re as _re_subj075
            _subj_075 = common_headers.get('subject', '')
            _has_re_prefix = bool(_re_subj075.match(r'^(Re:|RE:|re:|Fwd?:|FWD?:)\s*', _subj_075))

            if (_looks_forwarded or _has_re_prefix) and reply_to_email != source_email:
                if _looks_forwarded:
                    logger.info("🔗 Step 0.75: Searching for original thread (forwarded email)...")
                else:
                    logger.info("🔗 Step 0.75: Searching for original thread (Re: reply to existing conversation)...")
                try:
                    import re as _re_subj
                    # Strip Fwd:/Fw:/Re: prefixes to get the clean subject
                    _original_subject = common_headers.get('subject', '')
                    _clean_subject = _re_subj.sub(r'^(Re:\s*|Fwd?:\s*)+', '', _original_subject, flags=_re_subj.IGNORECASE).strip()

                    # Build search query: from the reply_to sender + matching subject
                    _search_query = f'from:{reply_to_email} subject:"{_clean_subject}"' if _clean_subject else f'from:{reply_to_email}'
                    logger.info("   🔍 Search query: %s", _search_query)

                    _search_response = requests.post(
                        f'{amp_base_url}/microsoft/integrations/search_messages',
                        headers={
                            'Authorization': f'Bearer {access_token}',
                            'Content-Type': 'application/json'
                        },
                        json={
                            'data': {
                                'search_query': _search_query,
                                'top': 5,
                                'include_body': False,
                            }
                        },
                        timeout=15
                    )

                    if _search_response.status_code == 200:
                        _search_result = _search_response.json()
                        _messages = _search_result.get('data', _search_result.get('messages', []))
                        # Handle both response shapes: {data: [...]} and {data: {messages: [...]}}
                        if isinstance(_messages, dict):
                            _messages = _messages.get('messages', [])
                        if not isinstance(_messages, list):
                            _messages = []

                        if _messages:
                            # Take the first (most relevant) match
                            _best_match = _messages[0]
                            original_message_id = _best_match.get('id')
                            original_conversation_id = _best_match.get('conversationId')
                            logger.info("   ✅ Found original thread! message_id=%s, conversationId=%s, subject='%s'",
                                       original_message_id,
                                       original_conversation_id,
                                       _best_match.get('subject', '?')[:80])
                        else:
                            logger.info("   ℹ️ No matching messages found — reply will be a new thread")
                    else:
                        logger.warning("   ⚠️ search_messages HTTP %d (non-fatal): %s",
                                      _search_response.status_code, _search_response.text[:200])
                except Exception as _search_err:
                    logger.warning("   ⚠️ Thread search failed (non-fatal, reply will be new thread): %s", _search_err)
            else:
                if reply_to_email == source_email:
                    logger.info("🔗 Step 0.75: Skipped — reply-to same as sender (no external thread)")
                else:
                    logger.info("🔗 Step 0.75: Skipped — not a forwarded or Re: email")

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

                # Build current date/TIME context for the LLM so it can resolve relative dates
                # Note: user_settings is loaded in Step 2 — not available here yet, use UTC default
                from datetime import datetime as _dt_now_cls, timezone as _tz_cls
                import pytz as _pytz_cls
                _user_tz = 'America/Chicago'  # default; Step 2 loads the real value
                try:
                    _tz_obj = _pytz_cls.timezone(_user_tz)
                    _now_local = _dt_now_cls.now(_tz_obj)
                except Exception:
                    _now_local = _dt_now_cls.utcnow()
                _today_str = _now_local.strftime('%Y-%m-%d')  # e.g. "2026-04-23"
                _today_weekday = _now_local.strftime('%A')     # e.g. "Wednesday"
                _now_time_str = _now_local.strftime('%I:%M %p') # e.g. "04:33 PM"

                _classify_system = f"""You are an AI assistant that classifies emails as meeting/scheduling requests and extracts meeting details.

TODAY'S DATE: {_today_str} ({_today_weekday})
CURRENT TIME: {_now_time_str} ({_user_tz})
USER TIMEZONE: {_user_tz}

Use today's date AND current time to resolve ALL relative dates/times in the email. Examples:
- "this Friday" → find the next Friday from {_today_str}
- "next Monday" → find the Monday of next week
- "tomorrow" → {(_dt_now_cls.now() + __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d')}
- "move to 5:30" → means {_today_str}T17:30:00 (today, unless context says otherwise)
- "push back an hour" → add 1 hour to the original meeting time; output the NEW time
Always output proposed_times in ISO 8601 format WITH the user's UTC offset (e.g. "2026-04-24T14:00:00-05:00").
IMPORTANT: When the sender mentions a SPECIFIC time ("at 5:30", "to 3pm", "an hour later"), you MUST resolve it to a concrete ISO 8601 datetime and include it in proposed_times. Never leave proposed_times empty if a specific time is stated or can be inferred.

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
{{
  "is_meeting_request": true/false,
  "intent": "new_request|confirm|reschedule|cancel|calendar_invite|no_action",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation",
  "duration_minutes": 30,
  "preferred_time": "afternoon",
  "preferred_days": ["tuesday", "wednesday"],
  "proposed_times": [],
  "current_event_time": null
}}

For "reschedule" or "cancel" intent: also extract "current_event_time" — the ISO 8601 datetime of the EXISTING meeting being moved or cancelled (e.g. "push our 3:30 meeting" → "{_today_str}T15:30:00 with UTC offset"). Set to null if not determinable.

INTENT values — pick exactly one:
- "new_request": someone is asking to schedule a NEW meeting, proposing times or asking for availability
- "confirm": someone is CONFIRMING a meeting that was ALREADY agreed upon and scheduled — both parties previously agreed (e.g. "confirmed for Thursday 2pm", "see you then!", "that works, talk Thursday", "just confirming our meeting tomorrow"). NOT for emails that are asking/checking if a time works for the first time — those are "new_request".
- "reschedule": someone wants to MOVE an existing meeting to a different time
- "cancel": someone wants to CANCEL an existing meeting
- "calendar_invite": a formal calendar invite or event invitation (You're invited to X on DATE)
- "no_action": not a scheduling email at all

For meeting requests, extract:
- duration_minutes: integer (look for "30-minute", "1 hour", etc; default 30)
- preferred_time: one of "morning", "afternoon", "evening", or null
- preferred_days: list of lowercase day names mentioned; empty list if no specific days mentioned
- proposed_times: CRITICAL — if the sender states or implies SPECIFIC times, resolve them to ISO 8601 datetimes and output them here. Examples:
  * "Friday at 2pm" → resolve Friday's date + 14:00
  * "move to 5:30" → {_today_str}T17:30:00 with UTC offset (today unless context says otherwise)
  * "push back an hour" → if original meeting is at 4:30, output {_today_str}T17:30:00 with offset
  * "an hour later" → compute original time + 1 hour
  Empty list ONLY if truly vague ("sometime this week", "whenever you're free").

For non-meeting emails, set duration_minutes=30, preferred_time=null, preferred_days=[], proposed_times=[]"""

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
                # Use AI-returned intent if present, otherwise fall back to is_meeting_request flag
                ai_intent = analysis_data.get('intent', '')
                valid_intents = ['new_request', 'confirm', 'reschedule', 'cancel', 'calendar_invite', 'no_action']
                if ai_intent in valid_intents:
                    intent = ai_intent
                else:
                    intent = 'new_request' if is_meeting_request else 'no_action'
                meeting_details = {
                    'duration_minutes': int(analysis_data.get('duration_minutes') or 30),
                    'preferred_time': analysis_data.get('preferred_time'),
                    'preferred_days': analysis_data.get('preferred_days') or [],
                    'proposed_times': analysis_data.get('proposed_times') or [],
                    'current_event_time': analysis_data.get('current_event_time'),  # ISO 8601 time of existing meeting being moved/cancelled
                }
                requires_review = confidence < 0.8  # temporary — recalculated after user_settings loads below

                logger.info("✅ Analysis complete: intent=%s, confidence=%.2f, is_meeting_request=%s", intent, confidence, is_meeting_request)
                logger.info("   Classification reasoning: %s", reasoning)
                logger.info("   requires_review (preliminary, confidence<0.8): %s", requires_review)
                logger.info("   meeting_details from AI: %s", meeting_details)
                logger.info("   AI-proposed times from email text: %s", meeting_details.get('proposed_times', []))

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
                                ExpressionAttributeValues={':s': 'completed', ':r': convert_floats_to_decimal({'action_taken': 'declined', 'confidence': confidence, 'reasoning': f'Non-scheduling intent: {intent}'}), ':c': _now, ':u': _now}
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

                # Find AI email signature (preferred) or fall back to primary
                # ai_email_signature_id in user_settings controls which sig AI-generated emails use.
                # Key absent  → not yet configured → use primary signature
                # Key = None  → user explicitly set "no signature" → primary_signature stays None
                # Key = "sig_..." → use that specific signature; fall back to primary if not found
                _KEY_NOT_SET = object()
                _ai_sig_id = user_settings.get('ai_email_signature_id', _KEY_NOT_SET)
                primary_signature = None

                def _find_primary(sigs):
                    for s in sigs:
                        if s.get('is_primary'):
                            return s
                    return sigs[0] if sigs else None

                if _ai_sig_id is _KEY_NOT_SET:
                    # Setting not yet configured — default to primary
                    primary_signature = _find_primary(signatures)
                elif _ai_sig_id is None:
                    # Explicitly "no signature" — leave primary_signature as None
                    primary_signature = None
                else:
                    # Specific signature ID chosen for AI emails
                    for sig in signatures:
                        if sig.get('signature_id') == _ai_sig_id:
                            primary_signature = sig
                            break
                    if not primary_signature:
                        # Chosen sig was deleted — fall back to primary
                        primary_signature = _find_primary(signatures)

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

                # Recalculate requires_review using the user's real confidence threshold now that user_settings is loaded
                _review_threshold = user_settings.get('auto_send_rules', {}).get('confidence_threshold', 0.8)
                requires_review = confidence < _review_threshold
                logger.info("   requires_review (final, confidence=%.2f < threshold=%.2f): %s", confidence, _review_threshold, requires_review)

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
                logger.info("   Full settings: automation_level=%s, calendar_mode=%s, auto_send_enabled=%s, confidence_threshold=%s",
                           user_settings.get('automation_level'),
                           user_settings.get('calendar_automation', {}).get('mode'),
                           user_settings.get('auto_send_rules', {}).get('enabled'),
                           user_settings.get('auto_send_rules', {}).get('confidence_threshold'))
                _sig_source = 'ai_email_sig' if (_ai_sig_id is not _KEY_NOT_SET and _ai_sig_id is not None) else ('none (explicitly)' if _ai_sig_id is None else 'primary (default)')
                logger.info("   Signatures found: %d (AI email sig: %s [%s])",
                           len(signatures),
                           primary_signature.get('name') if primary_signature else 'None',
                           _sig_source)

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
                                    ExpressionAttributeValues={':s': 'completed', ':r': convert_floats_to_decimal({'action_taken': 'declined', 'confidence': 0.0, 'reasoning': f'Sender blocked: {reply_to_email}'}), ':c': _now, ':u': _now}
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
            _offering_alternatives = False  # Outer-scope default — True only when explicit requested time was unavailable and we fell back to alternatives
            _polite_declining = False  # Outer-scope default — True only when requested time unavailable and auto_decline_mode=polite_decline
            try:
                # Only check calendar if intent requires scheduling
                if intent in ['schedule_new', 'propose_times', 'check_availability', 'new_request', 'reschedule', 'calendar_invite']:
                    duration_minutes = meeting_details.get('duration_minutes', 30)
                    preferred_time = meeting_details.get('preferred_time')  # e.g. "afternoon"
                    preferred_days = [d.lower() for d in (meeting_details.get('preferred_days') or [])]  # e.g. ["tuesday", "wednesday"]
                    # Request enough candidates to cover preferred days even when they are several
                    # days out. Each weekday has ~16-20 slots, so preferred days 6 days away require
                    # ~100+ raw slots before they appear. Request 250 when preferred days exist so
                    # the post-filter always has Friday/Monday (etc.) slots to choose from.
                    _max_slots_req = 250 if preferred_days else 5
                    logger.info("   📅 Calling check-availability: %s/scheduling/check-availability, duration=%dm, days_ahead=14, preferred_time=%s, preferred_days=%s",
                               api_base_url, duration_minutes, preferred_time, preferred_days)

                    # Extract actual business hours start/end from user settings (per-day map: {tuesday: {start_time, end_time}})
                    _biz_hours_cfg = user_settings.get('business_hours', {})
                    _biz_start_hr, _biz_end_hr = 9, 17  # safe defaults
                    if isinstance(_biz_hours_cfg, dict):
                        for _wd in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
                            _wd_cfg = _biz_hours_cfg.get(_wd, {})
                            if isinstance(_wd_cfg, dict) and _wd_cfg.get('enabled'):
                                try:
                                    _biz_start_hr = int(_wd_cfg.get('start_time', '09:00').split(':')[0])
                                    _biz_end_hr   = int(_wd_cfg.get('end_time',   '17:00').split(':')[0])
                                except (ValueError, AttributeError):
                                    pass
                                break
                    logger.info("   📅 Business hours from settings: %02d:00 - %02d:00", _biz_start_hr, _biz_end_hr)

                    # Call scheduling app's availability endpoint (uses Microsoft Graph + sophisticated algorithms)
                    _avail_payload = {
                        'user_id': sender_username,
                        'duration_minutes': duration_minutes,
                        'days_ahead': 14,  # Check next 2 weeks
                        'business_hours': [_biz_start_hr, _biz_end_hr],
                        'business_hours_only': True,
                        'max_slots': _max_slots_req,
                    }
                    if preferred_time:
                        _avail_payload['preferred_time'] = preferred_time
                    availability_response = requests.post(
                        f'{api_base_url}/scheduling/check-availability',
                        headers={
                            'Authorization': f'Bearer {access_token}',
                            'Content-Type': 'application/json'
                        },
                        json={'data': _avail_payload},
                        timeout=30
                    )

                    if availability_response.status_code == 200:
                        availability_result = availability_response.json()
                        if availability_result.get('success'):
                            _raw_slots = availability_result['data'].get('available_slots', [])
                            from datetime import datetime as _dt_parse, timedelta as _td_parse

                            # ── Branch A: sender stated EXPLICIT times ─────────────────────────
                            # e.g. "Friday at 2pm or Monday at 10am"
                            # Only show those exact requested times; do NOT generate random alternatives.
                            _explicit_times = meeting_details.get('proposed_times') or []
                            _offering_alternatives = False  # True when requested time was unavailable and we fell back to alternatives
                            _all_times_unavailable = False  # True when ALL requested times are conflicted (never/manual_review mode)
                            if _explicit_times:
                                # Build a set of available slot starts (minute-precision) for fast lookup
                                _avail_minutes = set()
                                for _sl in _raw_slots:
                                    try:
                                        _s = _dt_parse.fromisoformat(_sl.get('start', '').replace('Z', '+00:00'))
                                        _avail_minutes.add((_s.year, _s.month, _s.day, _s.hour, _s.minute))
                                    except Exception:
                                        pass

                                _available_explicit, _unavailable_explicit = [], []
                                _check_duration = meeting_details.get('duration_minutes', 30)
                                for _et in _explicit_times:
                                    try:
                                        _et_dt = _dt_parse.fromisoformat(str(_et).replace('Z', '+00:00'))
                                        # Check that ALL 15-min intervals within the requested duration are free.
                                        # Snap start to nearest 15-min boundary for the check.
                                        _rounded_start_min = (_et_dt.minute // 15) * 15
                                        _et_snapped = _et_dt.replace(minute=_rounded_start_min, second=0, microsecond=0)
                                        _all_free = all(
                                            (
                                                (_et_snapped + _td_parse(minutes=_i)).year,
                                                (_et_snapped + _td_parse(minutes=_i)).month,
                                                (_et_snapped + _td_parse(minutes=_i)).day,
                                                (_et_snapped + _td_parse(minutes=_i)).hour,
                                                (_et_snapped + _td_parse(minutes=_i)).minute,
                                            ) in _avail_minutes
                                            for _i in range(0, _check_duration, 15)
                                        )
                                        if _all_free:
                                            _available_explicit.append(_et)
                                        else:
                                            _unavailable_explicit.append(_et)
                                            logger.info("   🚫 Requested time %s conflicts — not all %d min free", _et, _check_duration)
                                    except Exception:
                                        _unavailable_explicit.append(_et)

                                logger.info("   📅 Explicit times check: available=%s, unavailable=%s",
                                           _available_explicit, _unavailable_explicit)

                                if _available_explicit:
                                    # At least one requested time is free — use those
                                    calendar_slots = [{'start': t} for t in _available_explicit]
                                    logger.info("   ✅ Using %d available explicitly-requested time(s)", len(calendar_slots))
                                else:
                                    # ALL requested times are unavailable — apply auto_decline_mode
                                    _cal_auto = user_settings.get('calendar_automation', {})
                                    _decline_mode = _cal_auto.get('auto_decline_mode', 'never')
                                    logger.info("   ⚠️ All explicitly requested times unavailable — auto_decline_mode=%s", _decline_mode)

                                    if _decline_mode == 'offer_alternatives':
                                        # Fall back to suggesting available times on those preferred days.
                                        # Do NOT book any of these yet — they will be proposed in a draft email.
                                        # A calendar hold is only created after the requester agrees to one.
                                        calendar_slots = _raw_slots[:5]
                                        _offering_alternatives = True
                                        logger.info("   📅 Offering alternative times (offer_alternatives mode) — no calendar hold until requester agrees")
                                    elif _decline_mode == 'polite_decline':
                                        # Polite decline — keep calendar_slots empty so no event is created.
                                        # The draft will be framed as a polite decline with no alternatives.
                                        calendar_slots = []
                                        _polite_declining = True
                                        logger.info("   📅 Polite decline mode — clearing slots, will draft polite decline")
                                    else:
                                        # 'never' — put in Action Needed with the requested (unavailable) times shown
                                        # so the dashboard user can see what was asked for and decide manually.
                                        # NOTE: _all_times_unavailable=True tells the draft generator NOT to offer
                                        # these times as available — they are shown in the card for human context only.
                                        calendar_slots = [{'start': t} for t in _explicit_times]
                                        _all_times_unavailable = True
                                        logger.info("   📋 All times unavailable — queuing for manual review with requested times shown")

                            # ── Branch B: vague request — generate slots on preferred days ────
                            elif preferred_days and _raw_slots:
                                _day_map = {
                                    'monday': 0, 'tuesday': 1, 'wednesday': 2,
                                    'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6
                                }
                                _preferred_dow = [_day_map[d] for d in preferred_days if d in _day_map]
                                def _slot_sort_key(s):
                                    try:
                                        _st = _dt_parse.fromisoformat(s.get('start', ''))
                                        _on_pref_day = 0 if _st.weekday() in _preferred_dow else 1
                                        _in_pref_time = 1
                                        if preferred_time:
                                            _hr = _st.hour
                                            _pt = preferred_time.lower()
                                            if 'morning' in _pt and 5 <= _hr < 12:
                                                _in_pref_time = 0
                                            elif 'afternoon' in _pt and 12 <= _hr < 17:
                                                _in_pref_time = 0
                                            elif 'evening' in _pt and 17 <= _hr < 21:
                                                _in_pref_time = 0
                                        return (_on_pref_day, _in_pref_time, _st)
                                    except Exception:
                                        return (1, 1, _dt_parse.min)
                                _raw_slots_sorted = sorted(_raw_slots, key=_slot_sort_key)
                                # Only keep slots that actually fall on the preferred days (no fill from other days)
                                _balanced, _day_counts = [], {}
                                for _sl in _raw_slots_sorted:
                                    try:
                                        _sl_dt = _dt_parse.fromisoformat(_sl.get('start', ''))
                                        if _sl_dt.weekday() not in _preferred_dow:
                                            continue  # strict — skip non-preferred days
                                        _sl_date = _sl_dt.date()
                                        _per_day_limit = max(2, 5 // max(len(preferred_days), 1)) + 1
                                        if _day_counts.get(_sl_date, 0) < _per_day_limit:
                                            _balanced.append(_sl)
                                            _day_counts[_sl_date] = _day_counts.get(_sl_date, 0) + 1
                                        if len(_balanced) >= 5:
                                            break
                                    except Exception:
                                        pass
                                calendar_slots = _balanced[:5]
                                logger.info("   📅 Vague request — preferred day filter: preferred_days=%s, raw_count=%d → kept %d on preferred days (days: %s)",
                                           preferred_days, len(_raw_slots), len(calendar_slots),
                                           {str(d): c for d, c in _day_counts.items()})
                            else:
                                calendar_slots = _raw_slots[:5]

                            logger.info("✅ Calendar availability: found %d slots", len(calendar_slots))
                            if calendar_slots:
                                logger.info("   First 5 available slots: %s",
                                           [s.get('start') for s in calendar_slots[:5]])
                            else:
                                logger.warning("   ⚠️ API returned success=true but 0 slots")
                        else:
                            logger.error("   ❌ Calendar API success=false: %s", availability_result.get('message', '')[:300])
                    else:
                        logger.error("   ❌ Calendar check HTTP %d: %s",
                                    availability_response.status_code, availability_response.text[:300])

                else:
                    logger.info("   ⏭️ Skipping calendar check — intent '%s' does not require availability lookup", intent)
            except Exception as e:
                logger.error("❌ Calendar availability check EXCEPTION: %s", e, exc_info=True)
                # Continue without calendar - draft can still be generated

            # ===========================================
            # STEP 2.55: LOOK UP EXISTING CALENDAR EVENT (for reschedule/cancel)
            # ===========================================
            # If the email is a reschedule or cancel request and the LLM extracted a
            # current_event_time, query the calendar around that time to find the
            # actual event being referenced.  The result is stored in ai_decision later.
            existing_event = None  # outer-scope default
            _current_event_time = meeting_details.get('current_event_time')
            if intent in ('reschedule', 'cancel') and _current_event_time:
                logger.info("🔍 Step 2.55: Looking up existing calendar event at %s...", _current_event_time)
                try:
                    from datetime import datetime as _ev_dt_cls, timedelta as _ev_td_cls
                    _ev_dt = _ev_dt_cls.fromisoformat(str(_current_event_time).replace('Z', '+00:00'))
                    # Search ±30 min around the stated meeting time
                    _ev_start = (_ev_dt - _ev_td_cls(minutes=30)).isoformat()
                    _ev_end   = (_ev_dt + _ev_td_cls(minutes=30)).isoformat()

                    _ev_response = requests.post(
                        f'{amp_base_url}/microsoft/integrations/get_events_between_dates',
                        headers={
                            'Authorization': f'Bearer {access_token}',
                            'Content-Type': 'application/json'
                        },
                        json={
                            'data': {
                                'user_id': sender_username,
                                'start_dt': _ev_start,
                                'end_dt': _ev_end,
                            }
                        },
                        timeout=15
                    )

                    if _ev_response.status_code == 200:
                        _ev_result = _ev_response.json()
                        # API may return a raw list OR a {success, data: {events: [...]}} wrapper
                        if isinstance(_ev_result, list):
                            _ev_list = _ev_result
                        elif isinstance(_ev_result, dict):
                            if _ev_result.get('success'):
                                _data = _ev_result.get('data', {})
                                if isinstance(_data, list):
                                    _ev_list = _data
                                else:
                                    _ev_list = _data.get('events', [])
                            else:
                                _ev_list = []
                                logger.warning("   ⚠️ get_events_between_dates returned success=false: %s",
                                               _ev_result.get('message', '')[:200])
                        else:
                            _ev_list = []

                        if _ev_list:
                            # Use the first (closest) matching event
                            _ev = _ev_list[0]
                            existing_event = {
                                'event_id': _ev.get('id') or _ev.get('event_id'),
                                'subject':  _ev.get('subject') or _ev.get('title', ''),
                                'start':    (_ev.get('start') or {}).get('dateTime') or _ev.get('start_time', ''),
                                'end':      (_ev.get('end')   or {}).get('dateTime') or _ev.get('end_time',   ''),
                                'location': (_ev.get('location') or {}).get('displayName') or _ev.get('location', ''),
                            }
                            logger.info("   ✅ Found existing event: id=%s, subject=%s, start=%s",
                                        existing_event['event_id'], existing_event['subject'], existing_event['start'])
                        else:
                            logger.info("   ℹ️ No events found in ±30 min window around %s", _current_event_time)
                    else:
                        logger.warning("   ⚠️ get_events_between_dates HTTP %d", _ev_response.status_code)
                except Exception as _ev_err:
                    logger.warning("   ⚠️ Existing event lookup failed (non-critical): %s", _ev_err)
            else:
                logger.info("   ⏭️ Step 2.55: Skipping existing event lookup (intent=%s, current_event_time=%s)",
                            intent, _current_event_time or 'none')

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
                logger.info("   automation_off check: calendar_has_active_setting=%s (mode=%s, accept=%s, reschedule=%s, cancel=%s, decline=%s)",
                           calendar_has_active_setting, cal_mode_check, cal_accept_check,
                           cal_reschedule_check, cal_cancel_check, cal_decline_check)
                if not calendar_has_active_setting:
                    logger.info("⏭️ Automation level is 'off' — saving request for manual review in Action Needed")

                    # Generate IDs since we haven't reached the normal flow yet
                    from datetime import datetime
                    import uuid as _uuid
                    _request_id = str(_uuid.uuid4())
                    _email_id = mail_data.get('messageId', _request_id)
                    _timestamp = datetime.now(timezone.utc).isoformat()
                    _original_subject = common_headers.get('subject', 'Meeting Request')

                    _ai_proposed = meeting_details.get('proposed_times', [])
                    _cal_fallback = [slot.get('start') for slot in (calendar_slots or [])[:5]]
                    _final_proposed = _ai_proposed or _cal_fallback
                    logger.info("   📅 proposed_times resolution: ai_extracted=%s, calendar_fallback=%s → using=%s",
                               _ai_proposed, _cal_fallback, _final_proposed)

                    _ai_decision = {
                        'intent': intent,
                        'confidence': confidence,
                        'requires_review': True,
                        'review_reason': 'Automation is off — manual review required',
                        'action_taken': 'none',
                        'auto_send_eligible': False,
                        'auto_send_blocked_reason': 'Automation is off',
                        'calendar_decision': {
                            'action': None,
                            'reason': 'Automation is off and no active calendar settings'
                        }
                    }

                    # Save request# record so it appears in Action Needed on the dashboard
                    try:
                        settings_table.put_item(Item=convert_floats_to_decimal({
                            'user_id': sender_username,
                            'storage_type': f'request#{_request_id}',
                            'created_at': _timestamp,
                            'updated_at': _timestamp,
                            'data': {
                                'request_id': _request_id,
                                'email_id': _email_id,
                                'draft_id': None,
                                'calendar_event_id': None,
                                'status': 'pending_review',
                                'sub_status': 'automation_off',
                                'ai_decision': _ai_decision,
                                'requester_email': reply_to_email,
                                'requester_name': reply_to_name,
                                'meeting': {
                                    'subject': _original_subject,
                                    'purpose': meeting_details.get('purpose', 'Meeting request'),
                                    'duration_minutes': meeting_details.get('duration_minutes', 30),
                                    'location_preference': meeting_details.get('location_pref', 'unspecified'),
                                    # Use AI-extracted times if the requester gave specific ones,
                                    # otherwise fall back to up to 5 slots from the user's own calendar
                                    # so the dashboard user can pick a time and hit Approve.
                                    'proposed_times': _final_proposed,
                                    'confirmed_time': None,
                                    'event_status': None,
                                    'attendee_count': 1 + len(common_headers.get('cc', []))
                                },
                                'source': 'email_forwarding',
                                'auto_processed': False,
                                'scheduled_at': None,
                                'completed_at': None,
                                'email_body': (email_body or '')[:500].strip()
                            }
                        }))
                        logger.info("✅ Saved automation_off request for manual review: request_id=%s", _request_id)
                        logger.info("   📋 Card data: requester=%s (%s), subject=%s, proposed_times_count=%d, times=%s",
                                   reply_to_name, reply_to_email, _original_subject,
                                   len(_final_proposed), _final_proposed)
                    except Exception as _e:
                        logger.error("❌ Failed to save automation_off request: %s", _e, exc_info=True)
                        _request_id = None  # Signal that save failed

                    # Update test record if applicable
                    if is_test_run and test_request_id:
                        try:
                            _now = datetime.now(timezone.utc).isoformat()
                            settings_table.update_item(
                                Key={'user_id': sender_username, 'storage_type': f'test#{test_request_id}'},
                                UpdateExpression='SET #data.#status = :s, #data.#result = :r, #data.processing.completed_at = :c, updated_at = :u',
                                ExpressionAttributeNames={'#data': 'data', '#status': 'status', '#result': 'result'},
                                ExpressionAttributeValues={
                                    ':s': 'completed',
                                    ':r': convert_floats_to_decimal({
                                        'action_taken': 'saved_for_review',
                                        'request_id': _request_id,
                                        'confidence': confidence,
                                        'reasoning': 'Automation is off — request saved for manual review in Action Needed',
                                        'settings_applied': {'automation_level': automation_level}
                                    }),
                                    ':c': _now,
                                    ':u': _now
                                }
                            )
                            logger.info("   ✅ Test record updated to completed (automation_off → Action Needed)")
                        except Exception as _te:
                            logger.warning("   ⚠️ Failed to update test record: %s", _te)

                    return {"result": {"skipped": False, "reason": "automation_off", "request_id": _request_id, "action": "saved_for_manual_review"}}
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
            # Extract CC recipients for later use in calendar event attendees.
            # Merge envelope CCs with any CCs parsed from the forwarded header block.
            cc_recipients = common_headers.get('cc', [])
            if isinstance(cc_recipients, str):
                cc_recipients = [cc_recipients]
            if _forwarded_cc:
                cc_recipients = list({*[c.lower() for c in cc_recipients], *_forwarded_cc})
            reschedule_mode = calendar_automation.get('reschedule_mode', 'never')  # never | draft_only | auto_reschedule
            cancellation_mode = calendar_automation.get('cancellation_mode', 'manual')  # manual | auto_confirm | auto_decline

            logger.info("   Calendar settings: mode=%s, decline=%s, accept=%s, reschedule=%s, cancel=%s",
                       cal_mode, auto_decline_mode, auto_accept_invites, reschedule_mode, cancellation_mode)
            logger.info("   Auto-send eligible: %s, blocked_reason: %s", should_auto_send, auto_send_blocked_reason or 'none')

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
                    # Business hours guard — runs immediately so ai_decision/request are saved with correct values
                    from datetime import datetime as _bh_dt
                    _bh_violations = []
                    _bh_ok_slots = []
                    for _bh_slot in calendar_slots:
                        try:
                            _bh_start = _bh_dt.fromisoformat(str(_bh_slot.get('start', '')).replace('Z', '+00:00'))
                            _bh_end = _bh_start + __import__('datetime').timedelta(minutes=meeting_details.get('duration_minutes', 30))
                            _bh_start_hr = _bh_start.hour
                            _bh_end_hr = _bh_end.hour + (_bh_end.minute / 60)
                            if _bh_start_hr < _biz_start_hr or _bh_end_hr > _biz_end_hr:
                                _bh_violations.append(_bh_slot)
                                logger.info("   ⚠️ Slot %s ends at %02d:%02d — outside business hours (%02d:00-%02d:00)",
                                            _bh_slot.get('start'), _bh_end.hour, _bh_end.minute, _biz_start_hr, _biz_end_hr)
                            else:
                                _bh_ok_slots.append(_bh_slot)
                        except Exception:
                            _bh_ok_slots.append(_bh_slot)
                    if _bh_violations and not _bh_ok_slots:
                        logger.info("   🚫 All slots outside business hours — overriding auto_reschedule → draft_reschedule")
                        calendar_action = 'draft_reschedule'
                        calendar_action_reason = "Requested reschedule time is outside business hours — needs manual review"
                        should_auto_send = False
                        auto_send_blocked_reason = "Requested reschedule time is outside business hours ({:02d}:00-{:02d}:00)".format(_biz_start_hr, _biz_end_hr)
                        requires_review = True
                        calendar_slots = []
                        logger.info("   ℹ️ auto_send blocked, slots cleared, card goes to Action Needed")
                elif reschedule_mode == 'draft_only':
                    calendar_action = 'draft_reschedule'
                    calendar_action_reason = "Drafting reschedule proposal (reschedule_mode=draft_only)"
                    logger.info("   ✅ Calendar action: draft_reschedule")
                else:
                    # reschedule_mode=never: still prepare a draft for review but show it
                    # inline in the Action Needed card (same as draft_only), just without
                    # automatic calendar operations.
                    calendar_action = 'draft_reschedule'
                    calendar_action_reason = "Drafting reschedule reply for manual review (reschedule_mode=never)"
                    logger.info("   📝 Calendar action: draft_reschedule (manual review mode)")

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
                    # cancellation_mode=manual/never: prepare draft for inline review in Action Needed card
                    calendar_action = 'draft_reschedule'
                    calendar_action_reason = "Drafting cancellation reply for manual review (cancellation_mode=manual)"
                    logger.info("   📝 Calendar action: draft_reschedule (manual cancel review mode)")

            elif intent in ['new_request', 'accept', 'confirm']:
                # New meeting request or acceptance - use cal_mode for event creation
                logger.info("   📅 Intent: %s - checking calendar mode", intent)
                if _offering_alternatives:
                    # The requested time was unavailable — we found alternatives to propose.
                    # Do NOT create a calendar hold: mutual agreement hasn't happened yet.
                    # The draft email will propose the alternatives and ask the requester to pick one.
                    # A hold is only created after they reply and agree to a specific time.
                    calendar_action = 'offer_alternatives'
                    calendar_action_reason = "Requested time unavailable — proposing alternatives via email (no hold until agreed)"
                    logger.info("   📅 Calendar action: offer_alternatives (no event created until requester agrees)")
                elif _polite_declining:
                    # Requested time was unavailable and user wants a polite decline (no alternatives).
                    # _polite_declining flag was set in Step 2.5 when auto_decline_mode=polite_decline.
                    calendar_action = 'polite_decline'
                    calendar_action_reason = "Requested time unavailable — politely declining (auto_decline_mode=polite_decline)"
                    logger.info("   📅 Calendar action: polite_decline (requested time unavailable, no alternatives offered)")
                elif cal_mode == 'always':
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
                    'existing_event': existing_event,
                    'calendar_decision': calendar_decision,
                    'calendar_action': calendar_action,
                    # Thread linking (populated by Step 0.75 for forwarded emails)
                    'original_message_id': original_message_id,
                    'original_conversation_id': original_conversation_id,
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
                            'sub_status': 'offer_alternatives_pending' if _offering_alternatives else 'polite_decline_pending' if _polite_declining else 'calendar_only_mode',
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
                                # Pre-set so the frontend approve button shows the correct label
                                'event_status': 'tentative' if (calendar_decision.get('action') if isinstance(calendar_decision, dict) else calendar_decision) in ('create_tentative', 'accept_tentative') else None,
                                'attendee_count': 1 + len(common_headers.get('cc', []))
                            },
                            'source': 'email_forwarding',
                            'auto_processed': True,
                            'scheduled_at': None,
                            'completed_at': None,
                            'email_body': (email_body or '')[:500].strip()
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

                    # Build times context from calendar_slots.
                    # IMPORTANT: when _all_times_unavailable is True the slots are stored for
                    # dashboard display only — they must NOT be offered to the requester as free times.
                    _times_context = ""
                    _all_times_unavailable_flag = locals().get('_all_times_unavailable', False)
                    if calendar_slots and not _all_times_unavailable_flag:
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
                            logger.info("   📅 Times injected into draft prompt: %s", _slots_formatted)
                    elif _all_times_unavailable_flag:
                        logger.info("   ⚠️ Requested times are UNAVAILABLE — NOT injecting into draft prompt as available times")
                    else:
                        logger.warning("   ⚠️ No calendar slots — draft will not propose specific times")

                    # Add calendar action context
                    _action_context = ""
                    if _all_times_unavailable_flag:
                        # The sender asked for a specific time that is ALREADY BUSY in the calendar.
                        # Do NOT offer that time back. Surface for manual review.
                        _conflict_times_str = ', '.join(
                            slot.get('start', '') for slot in calendar_slots[:3]
                        ) if calendar_slots else 'the requested time'
                        _action_context = (
                            f"\n\nSITUATION: The time(s) requested by the sender ({_conflict_times_str}) are "
                            "NOT available — there is already a conflict in the calendar at that time. "
                            "Do NOT offer or confirm these times. Instead:\n"
                            "1. Warmly acknowledge their request\n"
                            "2. Let them know that unfortunately that time doesn't work\n"
                            "3. Ask if they have other times that could work, OR let them know you will follow up with alternatives\n"
                            "Do NOT propose any specific new times — this reply goes to human review before sending."
                        )
                    elif calendar_action == 'offer_alternatives':
                        _action_context = (
                            "\n\nSITUATION: The time requested by the sender is NOT available in the calendar. "
                            "You must:\n"
                            "1. Politely acknowledge their meeting request\n"
                            "2. Apologize and explain that the requested time is unfortunately not available\n"
                            "3. Propose the AVAILABLE TIMES listed above as alternatives\n"
                            "4. Ask them which of those alternative times works best for them\n"
                            "Do NOT confirm the meeting or say it is scheduled — it is not yet agreed upon."
                        )
                    elif calendar_action == 'draft_reschedule' and auto_send_blocked_reason and 'business hours' in auto_send_blocked_reason.lower():
                        _action_context = (
                            f"\n\nSITUATION: The sender is requesting to reschedule to a time that falls outside working hours ({_biz_start_hr:02d}:00–{_biz_end_hr:02d}:00). "
                            "Do NOT confirm or agree to the requested time. Instead:\n"
                            "1. Acknowledge their request to reschedule\n"
                            "2. Gently let them know that the requested time falls outside of your usual working hours\n"
                            "3. Ask if they would be open to finding a time that works within normal business hours\n"
                            "Do NOT propose any specific times — this reply is going for human review before it is sent."
                        )
                    elif calendar_action and calendar_action != 'manual_review':
                        _action_context = f"\n\nCALENDAR ACTION: {calendar_action}. Reason: {calendar_action_reason or 'N/A'}"

                    # Current date/time so the LLM never proposes times in the past
                    try:
                        import pytz as _pytz_draft
                        _draft_tz = _pytz_draft.timezone(_user_tz if '_user_tz' in dir() else 'America/Chicago')
                        _draft_now = _dt_now_cls.now(_draft_tz)
                    except Exception:
                        _draft_now = _dt_now_cls.utcnow()
                    _draft_now_str = _draft_now.strftime('%A, %B %-d, %Y at %-I:%M %p')

                    _draft_user = f"""Generate a professional email response for the following:

CURRENT DATE/TIME: {_draft_now_str}
IMPORTANT: Do NOT suggest any times that have already passed. Only propose future times.

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
                    logger.info("   Draft preview (first 200 chars): %s", draft_text[:200].replace('\n', ' '))

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

                # Construct draft subject now (needed by ai_decision below)
                original_subject = common_headers.get('subject', 'Meeting Request')
                draft_subject = f"Re: {original_subject}" if not original_subject.startswith('Re:') else original_subject

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
                    # Original times extracted by LLM (preserved even when calendar_slots cleared by business hours guard)
                    'originally_requested_times': meeting_details.get('proposed_times', []),
                    # Existing calendar event that is being rescheduled/cancelled (populated by Step 2.55)
                    'existing_event': existing_event,
                    # Calendar automation decision
                    'calendar_decision': calendar_decision,
                    'calendar_action': calendar_action,
                    # For draft_reschedule: store draft text so frontend can send directly (no Outlook draft needed)
                    # Always store draft fields — card uses them for inline preview + Send Reply
                    'draft_text': draft_text,
                    'draft_subject': draft_subject,
                    'draft_to': reply_to_email,
                    'draft_content_type': draft_content_type,
                    # Thread linking (populated by Step 0.75 for forwarded emails)
                    'original_message_id': original_message_id,
                    'original_conversation_id': original_conversation_id,
                }

                logger.info("   🤖 AI Decision: intent=%s, confidence=%.2f, requires_review=%s, calendar_action=%s",
                           intent, confidence, requires_review, calendar_action or 'none')

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

                            # draft_reschedule items belong to Action Needed, not the Drafts pane
                            'source_flow': 'action_needed' if calendar_action == 'draft_reschedule' else 'drafts_pane',
                            'to_recipient': reply_to_email,

                            # ====== TIMESTAMPS ======
                            'sent_at': None
                        }
                    }

                    settings_table.put_item(Item=convert_floats_to_decimal(draft_item))
                    logger.info("✅ Draft saved to DynamoDB: draft_id=%s, request_id=%s", draft_id, request_id)
                    logger.info("   ↔️ Bidirectional link: draft#%s ←→ request#%s", draft_id[:8], request_id[:8])
                    logger.info("   Draft → to=%s, subject=%s", reply_to_email, draft_subject)

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
                            'completed_at': None,
                            'email_body': (email_body or '')[:500].strip()
                        }
                    }

                    storage_table.put_item(Item=convert_floats_to_decimal(scheduling_request_item))
                    logger.info("✅ Scheduling request saved: request_id=%s", request_id)
                    logger.info("   ↔️ Bidirectional link: request#%s ←→ draft#%s", request_id[:8], draft_id[:8])
                    logger.info("   📊 Status: %s (requires_review=%s, should_auto_send=%s)", lifecycle_status, requires_review, should_auto_send)
                    logger.info("   📅 Proposed times stored: %s",
                               [slot.get('start') for slot in calendar_slots[:5]] if calendar_slots else [])

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
                        # If we have the original message ID, reply in-thread; otherwise send new
                        if original_message_id:
                            logger.info("   📧 AUTO-SEND mode: Replying in-thread to %s...", original_message_id[:40])
                            send_response = requests.post(
                                f'{amp_base_url}/microsoft/integrations/reply_to_message',
                                headers={
                                    'Authorization': f'Bearer {access_token}',
                                    'Content-Type': 'application/json'
                                },
                                json={
                                    'data': {
                                        'message_id': original_message_id,
                                        'comment': draft_text,
                                    }
                                },
                                timeout=30
                            )
                        else:
                            logger.info("   📧 AUTO-SEND mode: Sending new email...")
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
                                outlook_message_id = send_result.get('data', {}).get('message_id') or send_result.get('data', {}).get('id')
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
                                    ExpressionAttributeValues={':status': 'auto_handled', ':ai_dec': convert_floats_to_decimal(ai_decision), ':updated': sent_timestamp}
                                )
                                logger.info("   📊 Request status updated to: auto_handled")
                            else:
                                logger.error("   ❌ Send email failed: %s", send_result.get('message'))
                                should_auto_send = False
                        else:
                            logger.error("   ❌ Send email API returned %d", send_response.status_code)
                            should_auto_send = False

                    # If NOT auto-sending, create Outlook draft for user review.
                    # draft_reschedule items also get a real draft so users who don't use the
                    # dashboard can still review / edit / send from Outlook directly.
                    if not should_auto_send:
                        if calendar_action == 'draft_reschedule':
                            # Create the Outlook draft just like the normal path, but keep
                            # the draft text in ai_decision too so the dashboard card can
                            # show it inline and offer "Edit before sending".
                            logger.info("   📝 draft_reschedule: creating Outlook draft for user review...")
                            try:
                                _draft_payload = {
                                    'user_id': sender_username,
                                    'to_recipients': [reply_to_email],
                                    'subject': draft_subject,
                                    'body': draft_text,
                                    'importance': 'normal',
                                    'content_type': draft_content_type,
                                }
                                if original_message_id:
                                    _draft_payload['reply_to_message_id'] = original_message_id
                                    logger.info("   🔗 Threaded reply draft (reply to %s)", original_message_id[:50])
                                draft_create_response = requests.post(
                                    f'{api_base_url}/scheduling/drafts/create',
                                    headers={
                                        'Authorization': f'Bearer {access_token}',
                                        'Content-Type': 'application/json'
                                    },
                                    json={'data': _draft_payload},
                                    timeout=30
                                )
                                if draft_create_response.status_code == 200:
                                    draft_create_result = draft_create_response.json()
                                    if draft_create_result.get('success'):
                                        outlook_draft_id = draft_create_result.get('data', {}).get('message_id') or draft_create_result.get('data', {}).get('id')
                                        logger.info("   ✅ Outlook draft created for draft_reschedule! Draft ID: %s", outlook_draft_id)
                                    else:
                                        logger.warning("   ⚠️ Draft creation returned success=false (non-fatal): %s",
                                                       draft_create_result.get('message', '')[:200])
                                else:
                                    logger.warning("   ⚠️ Draft creation HTTP %d (non-fatal): %s",
                                                   draft_create_response.status_code, draft_create_response.text[:200])
                            except Exception as _dr_err:
                                logger.warning("   ⚠️ Draft creation failed for draft_reschedule (non-fatal, card still works): %s", _dr_err)

                            action_taken = 'draft_created'
                            ai_decision['action_taken'] = 'draft_created'
                            ai_decision['outlook_draft_id'] = outlook_draft_id  # may be None if draft creation failed — card still works via ai_decision.draft_text
                            draft_timestamp = datetime.now(timezone.utc).isoformat()

                            settings_table.update_item(
                                Key={'user_id': sender_username, 'storage_type': f"draft#{draft_id}"},
                                UpdateExpression='SET #data.outlook_draft_id = :outlook_id, #data.#status = :status, updated_at = :updated',
                                ExpressionAttributeNames={'#data': 'data', '#status': 'status'},
                                ExpressionAttributeValues={':outlook_id': outlook_draft_id or 'none', ':status': 'outlook_draft_created' if outlook_draft_id else 'pending_review', ':updated': draft_timestamp}
                            )
                            storage_table.update_item(
                                Key={'user_id': sender_username, 'storage_type': f"request#{request_id}"},
                                UpdateExpression='SET #data.ai_decision = :ai_dec, #data.draft_id = :did, updated_at = :updated',
                                ExpressionAttributeNames={'#data': 'data'},
                                ExpressionAttributeValues={':ai_dec': convert_floats_to_decimal(ai_decision), ':did': draft_id, ':updated': draft_timestamp}
                            )
                            logger.info("   📊 Request status: pending_review (Action Needed — draft in Outlook + inline in card)")
                        else:
                            logger.info("   📝 Creating Outlook draft for user review...")
                            try:
                                # Build draft payload — conditionally thread if we found the original message
                                _norm_draft_payload = {
                                    'user_id': sender_username,
                                    'to_recipients': [reply_to_email],
                                    'subject': draft_subject,
                                    'body': draft_text,
                                    'importance': 'normal',
                                    'content_type': draft_content_type,
                                }
                                if original_message_id:
                                    _norm_draft_payload['reply_to_message_id'] = original_message_id
                                    logger.info("   🔗 Threaded reply draft (reply to %s)", original_message_id[:50])

                                # Call scheduler backend to create Outlook draft (proxies to Microsoft backend)
                                draft_create_response = requests.post(
                                    f'{api_base_url}/scheduling/drafts/create',
                                    headers={
                                        'Authorization': f'Bearer {access_token}',
                                        'Content-Type': 'application/json'
                                    },
                                    json={'data': _norm_draft_payload},
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
                                ai_decision['outlook_draft_id'] = outlook_draft_id  # carry on request so frontend can send it
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
                                    ExpressionAttributeValues={':ai_dec': convert_floats_to_decimal(ai_decision), ':updated': draft_timestamp}
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

                    # email_on_draft defaults to False — only notify if user has explicitly enabled it.
                    # For reschedule/cancel drafts the inline card already surfaces them; email noise is unwanted.
                    if action_taken == 'draft_created' and notification_prefs.get('email_on_draft', False):
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
                should_create_event = calendar_action in ['create_tentative', 'create_confirmed', 'accept', 'tentative', 'auto_reschedule']
                should_delete_event = calendar_action in ['confirm_cancel', 'auto_reschedule']

                # Determine event status based on calendar_action
                # Determine event_status:
                # For confirmed/accepted/rescheduled events, respect the user's 'Mark as Busy' setting.
                # For tentative proposals (create_tentative), also respect the setting — user explicitly chose.
                user_event_status = calendar_automation.get('event_status', 'tentative')
                if calendar_action == 'create_confirmed':
                    event_status_type = 'busy'  # confirmed meetings are always busy, not tentative
                elif calendar_action == 'auto_reschedule':
                    event_status_type = 'busy'  # rescheduled = both parties agreed → busy/scheduled
                elif calendar_action == 'accept':
                    event_status_type = user_event_status
                elif calendar_action in ['create_tentative', 'tentative']:
                    event_status_type = 'tentative'
                else:
                    event_status_type = 'tentative'

                logger.info("   📅 Calendar action=%s, should_create=%s, should_delete=%s, event_status=%s, slots_available=%d",
                           calendar_action or 'none', should_create_event, should_delete_event,
                           event_status_type, len(calendar_slots))

                # ===========================================
                # P2/P3: DELETE OR CANCEL EXISTING CALENDAR EVENT
                # For auto_reschedule: update (or delete+create) old event
                # For confirm_cancel: delete the existing calendar event
                # ===========================================
                existing_event_id = None
                _update_existing_event = False
                if should_delete_event:
                    # Look up existing calendar_event_id from prior scheduling requests
                    existing_request_storage_type = None
                    try:
                        existing_requests = storage_table.query(
                            KeyConditionExpression='user_id = :uid AND begins_with(storage_type, :prefix)',
                            ExpressionAttributeValues={
                                ':uid': sender_username,
                                ':prefix': 'request#'
                            },
                            ScanIndexForward=False,
                            Limit=20
                        )
                        _new_subj = common_headers.get('subject', '').lower()
                        # Also pull AI-extracted meeting title for looser matching
                        _ai_subj = meeting_details.get('subject', '').lower()
                        for req_item in existing_requests.get('Items', []):
                            req_data = req_item.get('data', {})
                            req_meeting = req_data.get('meeting', {})
                            _old_subj = req_meeting.get('subject', '').lower()
                            # Skip the current request itself
                            if req_item.get('storage_type', '') == f'request#{request_id}':
                                continue
                            # Match if there's any subject overlap (either direction) AND same requester
                            _subj_match = (
                                (_old_subj and _old_subj in _new_subj) or
                                (_old_subj and _new_subj and _new_subj in _old_subj) or
                                (_ai_subj and _old_subj and _ai_subj in _old_subj) or
                                (_ai_subj and _old_subj and _old_subj in _ai_subj)
                            )
                            if (req_data.get('calendar_event_id') and
                                req_data.get('requester_email') == reply_to_email and
                                _subj_match):
                                existing_event_id = req_data['calendar_event_id']
                                existing_request_storage_type = req_item.get('storage_type')
                                logger.info("   🔍 Found existing calendar event: %s (from %s)", existing_event_id, _old_subj)
                                break
                    except Exception as e:
                        logger.warning("   ⚠️ Could not look up existing event: %s", e)

                    if existing_event_id and calendar_action == 'auto_reschedule':
                        # For reschedule: prefer UPDATE over delete+create — preserves attendees/invites
                        # We'll attempt update_event; fall through to create if it fails
                        logger.info("   🔄 Will UPDATE existing event %s to new time (not delete+create)", existing_event_id)
                        # Mark for update — actual update happens below after we know the new slot
                        _update_existing_event = True
                    elif existing_event_id:
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
                        logger.info("   📝 No existing calendar event found to delete/update")
                else:
                    _update_existing_event = False

                # Business hours guard already ran earlier (at calendar_action decision time) —
                # if it fired, calendar_action is already 'draft_reschedule' and calendar_slots is [].

                # For 'confirm' intent, calendar availability check is skipped (no need to check free/busy
                # for a time that's already been agreed upon). Fall back to AI-extracted proposed_times.
                _effective_slots = calendar_slots
                if should_create_event and not _effective_slots and intent == 'confirm':
                    _proposed = meeting_details.get('proposed_times', [])
                    if _proposed:
                        _effective_slots = [{'start': t, 'end': None} for t in _proposed]
                        logger.info("   📅 Using AI-extracted confirmed time(s) for event: %s", _proposed)

                if should_create_event and _effective_slots:
                    # Extract meeting details from calendar slots or AI analysis
                    # For now, use first available slot
                    first_slot = _effective_slots[0]
                    logger.info("   📅 Using first slot for event: start=%s, end=%s",
                               first_slot.get('start'), first_slot.get('end'))

                    # Build attendees list: always include original sender;
                    # include CC'd recipients only if include_ccd_recipients is enabled
                    attendees = [source_email]
                    if calendar_automation.get('include_ccd_recipients') and cc_recipients:
                        attendees.extend([
                            addr.strip() for addr in cc_recipients
                            if addr.strip() and addr.strip() != source_email
                        ])

                    # Calculate end_time from start + duration (slots only carry 'start')
                    # duration_minutes may not be set in the calendar-only path — get it from meeting_details
                    try:
                        _duration_minutes = int(duration_minutes) if duration_minutes else meeting_details.get('duration_minutes', 30)
                    except (NameError, TypeError):
                        _duration_minutes = meeting_details.get('duration_minutes', 30)
                    _slot_start = first_slot.get('start', '') if isinstance(first_slot, dict) else str(first_slot)
                    try:
                        from datetime import datetime as _dt_cls, timedelta as _td_cls
                        _start_dt = _dt_cls.fromisoformat(_slot_start.replace('Z', '+00:00'))
                        _end_dt = _start_dt + _td_cls(minutes=_duration_minutes)
                        _slot_end = _end_dt.isoformat()
                    except Exception as _te:
                        logger.warning("   ⚠️ Could not calculate end_time: %s — falling back to start", _te)
                        _slot_end = _slot_start

                    # Format attendees as objects
                    _attendees_payload = [{'email': a, 'type': 'required'} for a in attendees if a]

                    # For reschedule: if we found the old event, UPDATE it instead of delete+create.
                    # This preserves attendees, invite chain, etc. Falls back to create if update fails.
                    if _update_existing_event and existing_event_id:
                        logger.info("   📅 Updating existing calendar event %s to new time via Graph API...", existing_event_id)
                        event_response = requests.post(
                            f'{amp_base_url}/microsoft/integrations/update_event',
                            headers={
                                'Authorization': f'Bearer {access_token}',
                                'Content-Type': 'application/json'
                            },
                            json={
                                'data': {
                                    'event_id': existing_event_id,
                                    'updated_fields': {
                                        'start': {'dateTime': _slot_start, 'timeZone': user_settings.get('timezone', 'America/Chicago')},
                                        'end': {'dateTime': _slot_end, 'timeZone': user_settings.get('timezone', 'America/Chicago')},
                                        'showAs': event_status_type,
                                    }
                                }
                            },
                            timeout=30
                        )
                        # If update succeeded, treat the existing event ID as the result
                        if event_response.status_code == 200 and event_response.json().get('success'):
                            logger.info("   ✅ Calendar event updated to new time!")
                        else:
                            logger.warning("   ⚠️ update_event failed (%s) — falling back to create_event", event_response.status_code)
                            _update_existing_event = False  # fall through to create below

                    if not _update_existing_event:
                        logger.info("   📅 Creating calendar event via Graph API...")
                        event_response = requests.post(
                            f'{amp_base_url}/microsoft/integrations/create_event',
                            headers={
                                'Authorization': f'Bearer {access_token}',
                                'Content-Type': 'application/json'
                            },
                            json={
                                'data': {
                                    'title': ('[TENTATIVE] ' if event_status_type == 'tentative' else '') + common_headers.get('subject', 'Meeting Request'),
                                    'description': f'Meeting scheduled via AI Scheduler\n\nOriginal request from: {sender_name} <{source_email}>',
                                    'start_time': _slot_start,
                                    'end_time': _slot_end,
                                    'attendees': _attendees_payload,
                                    'location': '',
                                    'time_zone': user_settings.get('timezone', 'America/Chicago'),
                                    'is_online_meeting': False,
                                    'send_invitations': 'send',
                                    'show_as': event_status_type,
                                }
                            },
                            timeout=30
                        )

                    if event_response.status_code == 200:
                        event_result = event_response.json()
                        if event_result.get('success'):
                            # For update_event the ID is the same existing one; for create_event it's new
                            calendar_event_id = (existing_event_id if _update_existing_event
                                                 else (event_result['data'].get('id') or event_result['data'].get('event_id')))
                            event_created = True
                            _op_label = "updated" if _update_existing_event else "created"
                            logger.info("   ✅ Calendar event %s! Event ID: %s", _op_label, calendar_event_id)
                            logger.info("      Status: %s", event_status_type)

                            event_timestamp = datetime.now(timezone.utc).isoformat()

                            # Update draft record with calendar event ID (only if a draft exists)
                            if draft_id:
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

                            # Update scheduling request record with calendar event and status.
                            # Rules:
                            # - auto_handled: email was auto-sent → keep auto_handled (don't demote to tentative_hold)
                            # - draft_created: a draft also exists → keep pending_review (Action Needed), add calendar info
                            # - calendar_only (no draft, no auto-send) → flip to tentative_hold or scheduled
                            if action_taken == 'auto_sent':
                                new_status = 'auto_handled'  # preserve: auto-send already finalised this
                            elif action_taken == 'draft_created':
                                new_status = 'pending_review'  # preserve: user still needs to review draft
                            elif event_status_type == 'busy':
                                new_status = 'scheduled'
                            else:
                                new_status = 'tentative_hold'
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
                            logger.info("   📊 Request status updated to: %s (calendar event created, automation=%s)", new_status, automation_level)
                        else:
                            logger.error("   ❌ Calendar event creation failed: %s", event_result.get('message'))
                    else:
                        logger.error("   ❌ Calendar event API returned %d", event_response.status_code)
                elif should_create_event and not _effective_slots:
                    logger.warning("   ⚠️ Wanted to create calendar event (action=%s) but 0 slots available", calendar_action)
                else:
                    if calendar_action == 'offer_alternatives':
                        logger.info("   ⏭️  No calendar hold created (offer_alternatives) — alternatives will be proposed in the draft email; hold is only created after the requester agrees to a specific time")
                    else:
                        logger.info("   ⏭️  Skipping calendar event (action=%s does not create event, mode=%s, intent=%s)",
                                   calendar_action or 'none', cal_mode, intent)

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
                if outlook_draft_id:
                    logger.info("📝 OUTLOOK DRAFT CREATED")
                    logger.info("   Draft ID: %s", outlook_draft_id)
                    logger.info("   User can review and send from Outlook")
                else:
                    logger.info("📋 DRAFT SAVED TO ACTION NEEDED (no Outlook draft — direct send via dashboard)")
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
            logger.info("   Calendar Slots Found: %d", len(calendar_slots))
            logger.info("   Calendar Event Created: %s (id=%s)", event_created, calendar_event_id or 'none')
            logger.info("   Proposed times on final record: %s",
                       [slot.get('start') for slot in calendar_slots[:5]] if calendar_slots else 'none')
            logger.info("   Requires Review: %s", requires_review)
            logger.info("=" * 60)

            # Update test record to completed if this was a test run
            _final_result = {
                "request_id": request_id,
                "draft_id": draft_id,
                "intent": intent,
                "confidence": confidence,
                "action_taken": action_taken,
                "calendar_action": calendar_action,
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

    