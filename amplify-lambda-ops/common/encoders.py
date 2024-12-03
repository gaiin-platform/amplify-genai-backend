import json
import decimal

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return int(obj)
        return super(DecimalEncoder, self).default(obj)

class CombinedEncoder(json.JSONEncoder):
    def default(self, obj):
        # Use the default DecimalEncoder for any other type it covers
        return DecimalEncoder.default(self, obj)