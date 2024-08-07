import json
import decimal
from pydantic import BaseModel, Field

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return int(obj)
        return super(DecimalEncoder, self).default(obj)

def pydantic_encoder(obj):
    if isinstance(obj, BaseModel):
        return obj.dict()
    raise TypeError(f"Object of type '{obj.__class__.__name__}' is not serializable")

class CombinedEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, BaseModel):
            return obj.model_dump()
        # Use the default DecimalEncoder for any other type it covers
        return DecimalEncoder.default(self, obj)