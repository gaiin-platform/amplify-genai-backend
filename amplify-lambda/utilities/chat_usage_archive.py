import boto3
from datetime import datetime
import os
import logging

# Initialize logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize the DynamoDB resource
dynamodb = boto3.resource('dynamodb')

# Get table names from environment variables
source_table_name = os.environ['CHAT_USAGE_DYNAMO_TABLE']
archive_table_name = os.environ['CHAT_USAGE_ARCHIVE_DYNAMO_TABLE']

# Initialize the tables
source_table = dynamodb.Table(source_table_name)
archive_table = dynamodb.Table(archive_table_name)

def get_current_quarter():
    month = datetime.now().month
    if month in [1, 2, 3]:
        return 1
    elif month in [4, 5, 6]:
        return 2
    elif month in [7, 8, 9]:
        return 3
    else:
        return 4

def get_quarter_start_dates():
    year = datetime.now().year
    return {
        1: f"{year}-01-01T00:00:00.000Z",
        2: f"{year}-04-01T00:00:00.000Z",
        3: f"{year}-07-01T00:00:00.000Z",
        4: f"{year}-10-01T00:00:00.000Z"
    }

def archive_items(event, context):
    try:
        current_quarter = get_current_quarter()
        quarter_start_dates = get_quarter_start_dates()
        
        date_ranges = []
        for i in range(1, 5):
            if i != current_quarter:
                quarter_start = quarter_start_dates[i]
                next_quarter = (i % 4) + 1
                next_quarter_start = quarter_start_dates[next_quarter]
                
                date_ranges.append((quarter_start, next_quarter_start))

        for start_date, end_date in date_ranges:
            response = source_table.query(
                IndexName='DateIndex',  # Ensure GSI on 'date' attribute exists
                KeyConditionExpression="#d BETWEEN :start_date AND :end_date",
                ExpressionAttributeNames={
                    "#d": "date"
                },
                ExpressionAttributeValues={
                    ":start_date": start_date,
                    ":end_date": end_date
                }
            )
            items = response['Items']
            
            while 'LastEvaluatedKey' in response:
                response = source_table.query(
                    IndexName='DateIndex',  
                    KeyConditionExpression="#d BETWEEN :start_date AND :end_date",
                    ExpressionAttributeNames={
                        "#d": "date"
                    },
                    ExpressionAttributeValues={
                        ":start_date": start_date,
                        ":end_date": end_date
                    },
                    ExclusiveStartKey=response['LastEvaluatedKey']
                )
                items.extend(response['Items'])
            
            for item in items:
                # Archive the item to the archive table
                try:
                    archive_table.put_item(Item=item)
                    # Optionally, delete the item from the source table
                    source_table.delete_item(Key={'PrimaryKey': item['PrimaryKey']})
                    logger.info(f"Archived and deleted item with PrimaryKey: {item['PrimaryKey']}")
                except Exception as e:
                    logger.error(f"Error archiving or deleting item with PrimaryKey: {item['PrimaryKey']}. Exception: {e}")

    except Exception as e:
        logger.error(f"Error during archiving process: {e}")
        return {
            'statusCode': 500,
            'body': 'An error occurred during archiving process.'
        }

    logger.info("Archiving completed successfully.")
    return {
        'statusCode': 200,
        'body': 'Archiving completed successfully.'
    }