import os
from usage.track_usage import handler


# Set environment variables as required by the track_usage function
os.environ["CHAT_USAGE_TABLE"] = "vu-amplify-dev-chat-usage"
os.environ["ADDITIONAL_CHARGES_TABLE"] = "chat-billing-dev-additional-charges"
os.environ["MODEL_EXCHANGE_RATE_TABLE"] = "chat-billing-dev-model-exchange-rates"
os.environ["HISTORY_USAGE_TABLE"] = "chat-billing-dev-history-usage"

# Define a test event
test_event = {}


# Define a context class with function_name attribute
class TestContext:
    function_name = "trackUsage_daily"  # Make sure this matches what your code uses to set the time range.


# Instantiate the test context
test_context = TestContext()

# Call the handler function with the test_event and test_context
response = handler(test_event, test_context)

# Output the response for verification
print(response)
