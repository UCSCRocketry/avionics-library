import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import sync_projects


class ConfigurationTests(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "projects.json")
            path.write_text(json.dumps(data), encoding="utf-8")
            return sync_projects.load_config(path)

    def test_empty_registry(self):
        self.assertEqual(self.load({"schema": 1, "projects": []}), [])

    def test_project_defaults(self):
        projects = self.load({
            "schema": 1,
            "projects": [{"repository": "UCSCRocketry/example"}],
        })
        self.assertEqual(projects, [{
            "repository": "UCSCRocketry/example",
            "branch": "main",
            "destination": "Libraries/Avionics",
            "base_ref": None,
        }])

    def test_invalid_repository_is_rejected(self):
        with self.assertRaises(sync_projects.SyncError):
            self.load({"schema": 1, "projects": [
                {"repository": "https://github.com/owner/repo"},
            ]})


if __name__ == "__main__":
    unittest.main()
