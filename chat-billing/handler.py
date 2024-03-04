import json
import logging

# Configure the logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# event: This is a JSON-formatted document that contains data for the Lambda function to process. In this case, it's expected to be a DynamoDB Stream event, which contains information about changes to the DynamoDB table.
# context: This provides runtime information to the handler, such as the function's execution environment (remaining time till timeout, AWS request ID, etc.).
def lambda_handler(event, context):
    # Log the received event
    logger.info("Received event: " + json.dumps(event, indent=2))

    # Process the DynamoDB stream records
    for record in event["Records"]:
        # Log the DynamoDB record
        logger.info("DynamoDB Record: " + json.dumps(record, indent=2))

    # Return a success response
    return {
        "statusCode": 200,
        "body": json.dumps("Processed {} records.".format(len(event["Records"]))),
    }
