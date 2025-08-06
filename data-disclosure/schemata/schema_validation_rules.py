from .save_data_disclosure_decision_schema import save_data_disclosure_decision_schema
from .upload_disclosure_schema import upload_disclosure_schema

rules = {
    "validators": {
        "/data-disclosure/check": {
            "check_data_disclosure_decision": {}  # uses query param
        },
        "/data-disclosure/save": {
            "save_data_disclosure_decision": save_data_disclosure_decision_schema
        },
        "/data-disclosure/latest": {"get_latest_data_disclosure": {}},  # get
        "/data-disclosure/upload": {"upload": upload_disclosure_schema},
    },
    "api_validators": {},
}
