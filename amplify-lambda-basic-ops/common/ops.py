from functools import wraps

from common import permissions
from service.routes import route_data


def op(tags=None, path="", name="", description="", params=None, method="POST"):
    # This is the actual decorator
    def decorator(func):
        def wrapper(*args, **kwargs):
            # You can do something with tags, name, description, and params here
            print(f"Path: {path}")
            print(f"Tags: {tags}")
            print(f"Name: {name}")
            print(f"Method: {method}")
            print(f"Description: {description}")
            print(f"Params: {params}")

            # Call the actual function
            result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator

def vop(tags=None, path="", name="", description="", params=None,  schema=None):
    # This is the actual decorator
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # You can do something with tags, name, description, and params here
            print(f"Path: {path}")
            print(f"Tags: {tags}")
            print(f"Name: {name}")
            print(f"Description: {description}")
            print(f"Params: {params}")

            if not permissions.permissions_by_state_type.get(path, None):

                operation = path.split("/")[-1]

                permissions.permissions_by_state_type[path] = {
                    operation: lambda user, data: True
                }

            # Call the actual function
            result = func(*args, **kwargs)
            return result

        route_data[path] = {
            'schema': schema,
            'tags': tags,
            'name': name,
            'description': description,
            'params': params,
            'handler': wrapper
        }

        return wrapper
    return decorator
