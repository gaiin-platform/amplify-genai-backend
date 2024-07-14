import os
import ast
from typing import List
from pydantic import BaseModel, field_validator
import os
import uuid
import boto3
from typing import List
from boto3.dynamodb.types import TypeSerializer
from pydantic import ValidationError
import os
import sys
import yaml
import argparse
from typing import Optional

dynamodb = boto3.resource('dynamodb')
serializer = TypeSerializer()

IGNORED_DIRECTORIES = {"node_modules", "venv", "__pycache__"}

def op(tags=None, path="", name="", description="", params=None):
    # This is the actual decorator
    def decorator(func):
        def wrapper(*args, **kwargs):
            # You can do something with tags, name, description, and params here
            print(f"Path: {path}")
            print(f"Tags: {tags}")
            print(f"Name: {name}")
            print(f"Description: {description}")
            print(f"Params: {params}")
            # Call the actual function
            result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator

