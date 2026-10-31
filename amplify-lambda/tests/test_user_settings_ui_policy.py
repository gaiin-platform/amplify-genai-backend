import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[1]
for path in (ROOT, ROOT / "state"):
    sys.path.insert(0, str(path))


def _install_import_stubs():
    """Allow testing the policy helper in lightweight local environments."""
    boto3 = ModuleType("boto3")
    boto3.resource = lambda *_args, **_kwargs: SimpleNamespace(Table=lambda _name: SimpleNamespace(scan=lambda **_kwargs: {}, get_item=lambda **_kwargs: {}))
    sys.modules.setdefault("boto3", boto3)

    conditions = ModuleType("boto3.dynamodb.conditions")
    conditions.Key = object
    conditions.Attr = object
    dynamodb = ModuleType("boto3.dynamodb")
    dynamodb.conditions = conditions
    sys.modules.setdefault("boto3.dynamodb", dynamodb)
    sys.modules.setdefault("boto3.dynamodb.conditions", conditions)

    pycommon = ModuleType("pycommon")
    pycommon.__path__ = []
    sys.modules.setdefault("pycommon", pycommon)
    authz = ModuleType("pycommon.authz")
    authz.validated = lambda *_args, **_kwargs: (lambda fn: fn)
    authz.setup_validated = lambda *_args, **_kwargs: None
    sys.modules.setdefault("pycommon.authz", authz)
    decorators = ModuleType("pycommon.decorators")
    decorators.required_env_vars = lambda *_args, **_kwargs: (lambda fn: fn)
    sys.modules.setdefault("pycommon.decorators", decorators)
    logger = ModuleType("pycommon.logger")
    logger.getLogger = lambda *_args, **_kwargs: SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None, debug=lambda *a, **k: None)
    sys.modules.setdefault("pycommon.logger", logger)

    dal = ModuleType("pycommon.dal")
    dal.__path__ = []
    sys.modules.setdefault("pycommon.dal", dal)
    providers = ModuleType("pycommon.dal.providers")
    providers.__path__ = []
    sys.modules.setdefault("pycommon.dal.providers", providers)
    aws = ModuleType("pycommon.dal.providers.aws")
    aws.__path__ = []
    sys.modules.setdefault("pycommon.dal.providers.aws", aws)
    resource_perms = ModuleType("pycommon.dal.providers.aws.resource_perms")
    resource_perms.DynamoDBOperation = SimpleNamespace(SCAN="scan")
    sys.modules.setdefault("pycommon.dal.providers.aws.resource_perms", resource_perms)

    schemata = ModuleType("schemata")
    schemata.__path__ = []
    sys.modules.setdefault("schemata", schemata)
    rules_module = ModuleType("schemata.schema_validation_rules")
    rules_module.rules = {}
    sys.modules.setdefault("schemata.schema_validation_rules", rules_module)
    permissions = ModuleType("schemata.permissions")
    permissions.get_permission_checker = lambda: None
    sys.modules.setdefault("schemata.permissions", permissions)

    state_package = ModuleType("state")
    state_package.__path__ = []
    sys.modules.setdefault("state", state_package)
    user_data = ModuleType("state.user_data")
    user_data.handle_get_item = lambda *_args, **_kwargs: None
    user_data.handle_put_item = lambda *_args, **_kwargs: None
    sys.modules.setdefault("state.user_data", user_data)


_install_import_stubs()
os.environ.setdefault("SHARES_DYNAMODB_TABLE", "test-shares-table")
MODULE_PATH = ROOT / "state" / "usersettings.py"
spec = importlib.util.spec_from_file_location("user_settings_ui_policy", MODULE_PATH)
usersettings = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = usersettings
spec.loader.exec_module(usersettings)


class UserSettingsUiPolicyTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "theme": "dark",
            "featureOptions": {"includeMemory": True},
            "hiddenModelIds": [],
            "uiPreference": "classic",
        }
        self.put = Mock(return_value={"uuid": "saved"})
        self.handle_put = patch.object(usersettings, "handle_put_item", self.put)
        self.handle_put.start()
        self.get_app_id = patch.object(usersettings, "get_app_id", return_value="app")
        self.get_app_id.start()

    def tearDown(self):
        self.handle_put.stop()
        self.get_app_id.stop()

    def test_disabled_policy_coerces_classic_to_new_without_dropping_settings(self):
        with patch.object(usersettings, "_classic_ui_switch_allowed", return_value=False):
            result = usersettings.save_settings_for_user("person", self.settings, "token")
        self.assertTrue(result["success"])
        self.assertEqual(self.put.call_args.args[4]["settings"], {**self.settings, "uiPreference": "new"})

    def test_allowed_policy_preserves_classic_preference(self):
        with patch.object(usersettings, "_classic_ui_switch_allowed", return_value=True):
            result = usersettings.save_settings_for_user("person", self.settings, "token")
        self.assertTrue(result["success"])
        self.assertEqual(self.put.call_args.args[4]["settings"], self.settings)

    def test_non_classic_settings_do_not_need_policy_lookup(self):
        settings = {**self.settings, "uiPreference": "new"}
        with patch.object(usersettings, "_classic_ui_switch_allowed") as allowed:
            result = usersettings.save_settings_for_user("person", settings, "token")
        allowed.assert_not_called()
        self.assertTrue(result["success"])
        self.assertEqual(self.put.call_args.args[4]["settings"], settings)

    def test_policy_lookup_failure_fails_closed_for_classic_saves(self):
        with patch.dict(os.environ, {"AMPLIFY_ADMIN_DYNAMODB_TABLE": "admin"}), patch.object(
            usersettings.boto3, "resource", side_effect=RuntimeError("offline")
        ):
            self.assertFalse(usersettings._classic_ui_switch_allowed())
            result = usersettings.save_settings_for_user("person", self.settings, "token")
        self.assertTrue(result["success"])
        self.assertEqual(self.put.call_args.args[4]["settings"]["uiPreference"], "new")

    def test_missing_deployment_config_keeps_legacy_allow_default(self):
        table = SimpleNamespace(get_item=Mock(return_value={}))
        with patch.dict(os.environ, {"AMPLIFY_ADMIN_DYNAMODB_TABLE": "admin"}), patch.object(
            usersettings.boto3, "resource", return_value=SimpleNamespace(Table=lambda _name: table)
        ):
            self.assertTrue(usersettings._classic_ui_switch_allowed())


if __name__ == "__main__":
    unittest.main()
