# Adding a New Email-based Agent Trigger

## Overview
To create a new email trigger for your agent, you'll add an event template that maps emails to agent prompts. The template defines how to transform incoming emails into agent instructions.

## Quick Setup

1. **Create Template**
```bash
curl -X POST /vu-agent/add-event-template \
-d '{
    "tag": "research",
    "prompt": [
        {
            "role": "system",
            "content": "You assist with research tasks. Analyze the email and create a research plan."
        },
        {
            "role": "user",
            "content": "New research request from ${sender}:\n\nSubject: ${subject}\n\n${contents}"
        }
    ],
    "assistantId": "your-assistant-id"
}'
```

2. **Allow Senders**
```bash
curl -X POST /vu-agent/add-allowed-sender \
-d '{
    "tag": "research",
    "sender": "collaborator@university.edu"
}'
```

3. **Use the Trigger**
   Send email to: `username+research@yourdomain.com`

## How It Works
1. Email arrives → SES → SNS → SQS
2. System extracts tag "research" from plus addressing
3. Looks up template by tag
4. Populates template variables with email content
5. Triggers agent with formatted prompt

That's it! The agent will now automatically process emails sent to your plus-addressed endpoint using your template's instructions.

# Email Template Variables and Formatting

When creating email templates, you can use several variables that will be automatically populated from the incoming email. The transformation from email to agent prompt happens in `to_agent_event()` within `email_events.py`, which uses Python's string Template system.

## Available Variables

The following variables are available in your templates:

```
${sender}     - The email address of the sender
${timestamp}  - When the email was received
${subject}    - The email subject line
${recipients} - List of email addresses the message was sent to
${contents}   - The email body (plain text preferred, falls back to HTML)
```

## Email Processing Flow

1. **Tag Detection**:
    - Primary tag comes from plus addressing (e.g., `user+scheduling@domain.com`)
    - Secondary tags can be included as hashtags in the subject line
    - Tags are processed in order: plus address tag first, then subject hashtags

2. **Content Processing**:
   ```python
   email_details = {
       'sender': source_email,
       'timestamp': mail_data['timestamp'],
       'subject': mail_data['commonHeaders']['subject'],
       'recipients': mail_data['destination'],
       'contents': parsed_email['body_plain'] or parsed_email['body_html']
   }
   ```

3. **Template Population**:
   The system uses Python's `Template.safe_substitute()` to fill in variables, ensuring the template won't fail if a variable is missing. For example, this template entry:
   ```json
   {
       "role": "user",
       "content": "Email from ${sender} at ${timestamp} says:\n\n${contents}"
   }
   ```
   Becomes:
   ```json
   {
       "role": "user",
       "content": "Email from alice@example.com at 2024-01-20T15:30:00Z says:\n\nCan we schedule a meeting?"
   }
   ```

4. **File Handling**:
    - Email attachments are automatically stored in S3
    - File metadata is tracked in DynamoDB
    - Files are tagged with both the email tag and any subject hashtags

## Template Best Practices

1. Always include key email metadata (sender, timestamp) for context
2. Use clear delimiters around email content for better parsing
3. Consider adding contextual markers like:
   ```
   Original Email:
   From: ${sender}
   Subject: ${subject}
   Time: ${timestamp}
   
   Content:
   '''
   ${contents}
   '''
   ```

# Email to Agent: The Complete Processing Chain

## Processing Flow
1. Email → SES (AWS Simple Email Service)
2. SES → SNS (Simple Notification Service)
3. SNS → SQS (Simple Queue Service)
4. SQS → `agent_queue.route_queue_event()`
5. Route → `SESMessageHandler.process()`
6. Process → `process_email()`
7. Email Processing → `to_agent_event()`
8. Event → `handlers.handle_event()`

## Code Flow Breakdown

### Initial Queue Processing
When a message hits our SQS queue, `agent_queue.py` takes control:
```python
def route_queue_event(event, context):
    # Loops through records from SQS
    # Finds appropriate handler (SESMessageHandler in this case)
    # Calls handler.process()
```

### Email Message Handling
`SESMessageHandler` in `email_events.py` is our specialized handler for email:
```python
class SESMessageHandler(MessageHandler):
    def process(self, message, context):
        # Converts SNS message format to expected email format
        # Calls process_email()
```

### Email Processing Chain
`process_email()` in `email_events.py` does the heavy lifting:
1. Validates email (spam check, virus check)
2. Parses destination email for plus addressing
3. Checks sender permissions via `is_allowed_sender()`
4. Routes valid emails to `to_agent_event()`

### Event Template Processing
`to_agent_event()` transforms emails into agent events:
1. Extracts email details (sender, subject, body)
2. Looks up matching template using `get_event_template()`
3. Fills template variables using Python's Template system
4. Creates final event payload with:
    - Formatted prompt
    - Metadata
    - Session ID
    - User information

### Agent Handling
Finally, `handlers.handle_event()` takes over:
1. Sets up working directory
2. Configures agent environment
3. Processes template instructions
4. Executes agent actions
5. Stores results in S3/DynamoDB

### Key Support Functions
- `extract_email_body_and_attachments()`: Parses raw email content
- `save_email_to_s3()`: Archives original email
- `parse_email()`: Handles plus addressing extraction
- `find_hash_tags()`: Extracts tags from subject lines

### State Management
Throughout this process, several state management operations occur:
1. Email contents archived to S3
2. Attachments stored separately
3. File metadata tracked in DynamoDB
4. Template lookups cached when possible
5. Agent state maintained between operations

The entire chain ensures reliable email processing, template matching, and agent execution while maintaining a complete audit trail of all operations.

This modular approach allows for easy additions of new handlers (beyond email) and templates, while maintaining a consistent processing pattern for all agent interactions.

