import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).parents[1] / "service" / "core.py"


class DeploymentDefaultsTests(unittest.TestCase):
    def test_admin_default_storage_and_feature_availability(self):
        source = MODULE_PATH.read_text()
        self.assertIn('item["data"] = "future-cloud"', source)
        deployment_config = source.split('elif config_type == AdminConfigTypes.DEPLOYMENT_FEATURES:', 1)[1].split('\n    else:', 1)[0]
        self.assertIn('"promptHighlighter": False', deployment_config)
        self.assertIn('"memory": False', deployment_config)


if __name__ == "__main__":
    unittest.main()
