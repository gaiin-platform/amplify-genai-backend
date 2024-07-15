from pydantic import BaseModel

from common import permissions


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

def vop(tags=None, path="", name="", description="", params=None, model=BaseModel):
    # This is the actual decorator
    def decorator(func):
        def wrapper(*args, **kwargs):
            # You can do something with tags, name, description, and params here
            print(f"Path: {path}")
            print(f"Tags: {tags}")
            print(f"Name: {name}")
            print(f"Description: {description}")
            print(f"Params: {params}")

            if not permissions.permissions_by_state_type.get(path, None):
                permissions.permissions_by_state_type[path] = {
                    'qa_check': lambda user, data: True
                }

            # Call the actual function
            result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator
