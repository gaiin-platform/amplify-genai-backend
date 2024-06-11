
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import json
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj)
        return super().default(obj)

def pydantic_encoder(obj):
    if isinstance(obj, BaseModel):
        return obj.dict()
    raise TypeError(f"Object of type '{obj.__class__.__name__}' is not serializable")

class CombinedEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, BaseModel):
            return obj.model_dump()
        if isinstance(obj, datetime):  # Handle datetime objects
            return obj.isoformat()
        return DecimalEncoder().default(obj)  # Fallback to DecimalEncoder for other types