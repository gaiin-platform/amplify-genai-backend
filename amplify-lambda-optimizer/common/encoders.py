import json
import decimal

from pydantic import BaseModel


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return int(obj)
        return super(DecimalEncoder, self).default(obj)


def custom_encoder(obj):
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"Object of type '{obj.__class__.__name__}' is not serializable")


class CombinedEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, BaseModel):
            return obj.dict()
        if isinstance(obj, dict):
            return obj
        # Use the default DecimalEncoder for any other type it covers
        return DecimalEncoder.default(self, obj)