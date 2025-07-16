from .save_artifact_schema import save_artifact_schema
from .share_artifact_schema import share_artifact_schema

rules = {
    "validators": {
        "/artifacts/get_all": {"read": {}},  # get
        "/artifacts/get": {"read": {}},  # get
        "/artifacts/delete": {"delete": {}},  # delete
        "/artifacts/save": {"save": save_artifact_schema},
        "/artifacts/share": {"share": share_artifact_schema},
    },
    "api_validators": {},
}
