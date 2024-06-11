
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import unittest
import yaml
from generator.crud import update_yml  # Assuming the function is in generator/crud.py

class TestUpdateYml(unittest.TestCase):

  def setUp(self):
    self.server_yaml = """
        service: someService
        provider:
            name: aws
            runtime: python3.11
            stage: dev
        resources:
            Resources: {}
        """

  def test_update_yml_simple(self):

    dsl_json = """
        {
            "dataType": "TestDataType",
            "schema": {
                "id": {
                    "type": "string"
                },
                "user": {
                    "type": "string"
                },
                "name": {
                    "type": "string"
                }
            },
            "queries": [
                {
                    "name": "byUser",
                    "hashKey": "user"
                }
            ]
        }
        """

    expected_table_key = "TestDataTypeTable"
    updated_yml = update_yml(self.server_yaml, dsl_json)
    updated_dict = yaml.safe_load(updated_yml)
    self.assertIn(expected_table_key, updated_dict["resources"]["Resources"])

  def test_update_yml_complex(self):

    dsl_json = """
        {
            "dataType": "AnotherDataType",
            "schema": {
                "id": {
                    "type": "string"
                },
                "user": {
                    "type": "string"
                },
                "location": {
                    "type": "string"
                }
            },
            "queries": [
                {
                    "name": "byId",
                    "hashKey": "id",
                    "rangeKey": "location"
                }
            ]
        }
        """

    expected_table_key = "AnotherDataTypeTable"
    updated_yml = update_yml(self.server_yaml, dsl_json)
    updated_dict = yaml.safe_load(updated_yml)
    self.assertIn(expected_table_key, updated_dict["resources"]["Resources"])

    # Test presence of rangeKey in GSI
    gsi = updated_dict["resources"]["Resources"][expected_table_key]["Properties"]["GlobalSecondaryIndexes"]
    self.assertEqual(gsi[0]["KeySchema"][1]["AttributeName"], "location")

  def test_update_yml_error(self):

    dsl_json = """
        {
            "dataType": "IncompleteDataType",
            "schema": {
                "id": {
                    "type": "string"
                },
                "user": {
                    "type": "string"
                }
            },
            "queries": [
                {
                    "name": "byLocation",
                    "hashKey": "location"
                }
            ]
        }
        """

    with self.assertRaises(KeyError):
      update_yml(self.server_yaml, dsl_json)

if __name__ == '__main__':
  unittest.main()
