from common.validate import validated


# Look in validate.py and permissions.py
# for sample and how it is used
#
@validated(op="sample")
def sample(event, context, current_user, name, data):
    data = data['data']

    print(f"User {current_user} requested {data}")

    return {"success": True,
            "message": "Sample response",
            "data": {"msg": "Sample response"}}
