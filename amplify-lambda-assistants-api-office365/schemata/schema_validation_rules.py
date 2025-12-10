validators = {
    "/microsoft/integrations": {"get": {}},
    "/integrations/email/webhook": {"post": {}, "get": {}},
    "/integrations/email/subscription/create": {"post": {}},
    "/integrations/email/user-guid": {"get": {}},
    "/integrations/email/organization/users": {"get": {}}
}

rules = {"validators": validators, "api_validators": validators}
