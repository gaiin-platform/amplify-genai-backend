
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

