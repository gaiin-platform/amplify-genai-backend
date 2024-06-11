
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import yaml
import json


def sanity_check_dsl(dsl):
  try:
    # Load JSON
    dsl_dict = json.loads(dsl)
  except json.JSONDecodeError:
    raise ValueError("Invalid JSON provided in DSL")

  # Check for required top-level keys
  for key in ["dataType", "schema", "queries"]:
    if key not in dsl_dict:
      raise ValueError(f"Missing required key: {key}")

  # Check if 'schema' and 'queries' are not empty
  if not dsl_dict["schema"]:
    raise ValueError(f"'schema' can't be empty")

  if not dsl_dict["queries"]:
    raise ValueError(f"'queries' can't be empty")

  # Check all queries
  for query in dsl_dict['queries']:
    # Check hashKey in schema
    if query['hashKey'] not in dsl_dict['schema']:
      raise ValueError(f"hashKey {query['hashKey']} in queries is not defined in the schema")

    # If rangeKey is present, check it in schema
    if 'rangeKey' in query and query['rangeKey'] not in dsl_dict['schema']:
      raise ValueError(f"rangeKey {query['rangeKey']} in queries is not defined in the schema")

  return True


def update_yml(serveryml, dsl):

  sanity_check_dsl(dsl)

  # Load the serverless yml and dsl json to Python dictionary
  server_dict = yaml.safe_load(serveryml)
  dsl_dict = json.loads(dsl)

  # Define table name
  table_name = '${self:service}-${sls:stage}-' + dsl_dict['dataType']

  # Keys used in key schemas and indexes
  keys = set(query['hashKey'] for query in dsl_dict['queries'])
  keys.update(query['rangeKey'] for query in dsl_dict['queries'] if 'rangeKey' in query)

  # Check that all keys used in queries are defined in the schema
  for key in keys:
    if key not in dsl_dict['schema']:
      raise KeyError(f"{key} in queries is not defined in the schema")

  # Prepare Attribute Definitions
  attr_defs = [{"AttributeName": key, "AttributeType": "S"} for key in keys]

  # Define Key Schema
  hash_parts = [{'AttributeName': query['hashKey'], 'KeyType': 'HASH'} for query in dsl_dict['queries'] if 'hashKey' in query]
  range_parts = [{'AttributeName': query['rangeKey'], 'KeyType': 'RANGE'} for query in dsl_dict['queries'] if 'rangeKey' in query]
  key_schema = hash_parts + range_parts

  # Construct DynamoDB Table per DSL
  new_table = {
    'Type': 'AWS::DynamoDB::Table',
    'Properties': {
      'TableName': table_name,
      'AttributeDefinitions': attr_defs,
      'KeySchema': key_schema,
      'ProvisionedThroughput': {
        'ReadCapacityUnits': 1,
        'WriteCapacityUnits': 1
      }
    }
  }

  # Prepare Secondary Indexes
  for query in dsl_dict['queries']:
    if 'rangeKey' in query:
      gsi = {
        'IndexName': query['name'],
        'KeySchema': [
          {'AttributeName': query['hashKey'], 'KeyType': 'HASH'},
          {'AttributeName': query['rangeKey'], 'KeyType': 'RANGE'}
        ],
        'ProvisionedThroughput': {
          'ReadCapacityUnits': 1,
          'WriteCapacityUnits': 1
        },
        'Projection': {
          'ProjectionType': 'ALL'
        }
      }
      new_table['Properties'].setdefault('GlobalSecondaryIndexes', []).append(gsi)

  # Update DynamoDB Table definition in original serverless yml
  table_key = f"{dsl_dict['dataType']}Table"
  server_dict["resources"]["Resources"][table_key] = new_table

  # Convert the Python dictionary back to YAML
  new_yml = yaml.dump(server_dict, sort_keys=False)
  return new_yml
