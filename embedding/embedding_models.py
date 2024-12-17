from decimal import Decimal
import os
import boto3
dynamodb = boto3.resource('dynamodb')


def get_embedding_models():
    model_rate_table = dynamodb.Table(os.environ["MODEL_RATE_TABLE"])
    defaults = {
        'embedding': None,
        'qa': None
    }
    try:
        # Retrieve all items from the DynamoDB table
        response = model_rate_table.scan()
        items = response.get('Items', [])

        # Check if there are more items (pagination)
        while 'LastEvaluatedKey' in response:
            response = model_rate_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))

        # Filter and find the default embedding and QA models
        for item in items:
            if item.get('DefaultEmbeddingsModel') is True:
                defaults['embedding'] = {'model_id': item['ModelID'], 'provider': item['Provider']}
            if item.get('DefaultQAModel') is True:
                defaults['qa'] = {'model_id': item['ModelID'], 'provider': item['Provider']}

    except Exception as e:
        return {"success": False, "message": f"Error retrieving default models: {str(e)}"}

    # Check if both default models were found
    if not defaults['embedding'] or not defaults['qa']:
        return {"success": False, "message": "Could not find all default models"}

    return {"success": True, "data": defaults}