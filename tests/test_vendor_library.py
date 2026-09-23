import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import vendor_library as vendor


def symbol(name, value):
    return (f'\t(symbol "{name}"\n'
            f'\t\t(property "Value" "{value}")\n'
            f'\t)')


def library(*symbols):
    return ('(kicad_symbol_lib\n'
            '\t(version 20231120)\n'
            '\t(generator_version "8.0")\n' +
            '\n'.join(symbols) + '\n)\n')


def footprint(name, model="model.step"):
    return (f'(footprint "{name}"\n'
            '\t(layer "F.Cu")\n'
            f'\t(model "${{KIPRJMOD}}/Models/{model}"\n'
            '\t\t(offset (xyz 0 0 0))\n'
            '\t\t(scale (xyz 1 1 1))\n'
            '\t\t(rotate (xyz 0 0 0))\n'
            '\t)\n'
            ')\n')


class VendorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.project = self.root / "project"
        self.source.mkdir()
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=self.source, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=self.source, check=True)
        self.write_source(library(symbol("A", "base")), b"base-model")
        self.base_commit = self.commit("base")

    def tearDown(self):
        self.temporary.cleanup()

    def write_source(self, symbols, model):
        (self.source / vendor.SYMBOL_LIBRARY).write_text(
            symbols, encoding="utf-8")
        footprints = self.source / vendor.FOOTPRINT_DIRECTORY
        models = self.source / vendor.MODEL_DIRECTORY
        footprints.mkdir(exist_ok=True)
        models.mkdir(exist_ok=True)
        (footprints / "FP.kicad_mod").write_text(
            footprint("FP"), encoding="utf-8")
        (models / "model.step").write_bytes(model)

    def commit(self, message):
        subprocess.run(["git", "add", "."], cwd=self.source, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message],
                       cwd=self.source, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.source, check=True,
            capture_output=True, text=True).stdout.strip()

    @property
    def destination(self):
        return self.project / "Libraries" / "Avionics"

    def install(self):
        return vendor.update(self.project, "Libraries/Avionics", self.source,
                             source_name="test/library")

    def test_initial_install_records_commit_and_rewrites_model_path(self):
        result = self.install()
        manifest = json.loads((self.destination / vendor.MANIFEST).read_text())
        self.assertEqual(manifest["commit"], self.base_commit)
        self.assertFalse(manifest["modified"])
        footprint_text = (self.destination / vendor.FOOTPRINT_DIRECTORY /
                          "FP.kicad_mod").read_text()
        self.assertIn("${KIPRJMOD}/Libraries/Avionics/Models/model.step",
                      footprint_text)
        self.assertEqual(result["modified_paths"], [])

    def test_model_path_rewrite_round_trip(self):
        files = {
            "Avionics_Feet.pretty/FP.kicad_mod": footprint("FP").encode()
        }
        project = vendor.projectize_model_paths(files, "Libraries/Avionics")
        canonical = vendor.canonicalize_model_paths(
            project, "Libraries/Avionics")
        self.assertEqual(canonical, files)

    def test_clean_update(self):
        self.install()
        self.write_source(library(symbol("A", "upstream")), b"new-model")
        new_commit = self.commit("update")
        result = self.install()
        text = (self.destination / vendor.SYMBOL_LIBRARY).read_text()
        self.assertIn('"upstream"', text)
        self.assertEqual((self.destination / vendor.MODEL_DIRECTORY /
                          "model.step").read_bytes(), b"new-model")
        self.assertEqual(result["new_commit"], new_commit)

    def test_upstream_symbol_header_update_is_not_marked_local(self):
        self.install()
        path = self.source / vendor.SYMBOL_LIBRARY
        path.write_text(path.read_text().replace(
            '(version 20231120)', '(version 20241209)'))
        self.commit("update header")
        result = self.install()
        text = (self.destination / vendor.SYMBOL_LIBRARY).read_text()
        self.assertIn('(version 20241209)', text)
        self.assertEqual(result["modified_paths"], [])

    def test_update_preserves_unmanaged_project_files(self):
        self.install()
        note = self.destination / "README.local"
        note.write_text("project notes", encoding="utf-8")
        self.write_source(library(symbol("A", "upstream")), b"base-model")
        self.commit("update")
        self.install()
        self.assertEqual(note.read_text(encoding="utf-8"), "project notes")

    def test_independent_local_and_upstream_changes_merge(self):
        self.install()
        path = self.destination / vendor.SYMBOL_LIBRARY
        path.write_text(path.read_text().replace('"base"', '"local"'))
        self.write_source(library(symbol("A", "base"), symbol("B", "new")),
                          b"base-model")
        self.commit("add B")
        result = self.install()
        text = path.read_text()
        self.assertIn('"local"', text)
        self.assertIn('(symbol "B"', text)
        self.assertEqual(result["modified_paths"], [vendor.SYMBOL_LIBRARY])
        manifest = json.loads((self.destination / vendor.MANIFEST).read_text())
        self.assertTrue(manifest["modified"])

    def test_conflict_aborts_without_changing_project(self):
        self.install()
        path = self.destination / vendor.SYMBOL_LIBRARY
        path.write_text(path.read_text().replace('"base"', '"local"'))
        before = path.read_bytes()
        self.write_source(library(symbol("A", "upstream")), b"base-model")
        self.commit("change A")
        with self.assertRaisesRegex(vendor.VendorError, "conflict"):
            self.install()
        self.assertEqual(path.read_bytes(), before)
        manifest = json.loads((self.destination / vendor.MANIFEST).read_text())
        self.assertEqual(manifest["commit"], self.base_commit)

    def test_binary_model_conflict_aborts(self):
        self.install()
        model = self.destination / vendor.MODEL_DIRECTORY / "model.step"
        model.write_bytes(b"local-model")
        self.write_source(library(symbol("A", "base")), b"upstream-model")
        self.commit("change model")
        with self.assertRaisesRegex(vendor.VendorError, "model.step"):
            self.install()
        self.assertEqual(model.read_bytes(), b"local-model")

    def test_missing_referenced_model_is_rejected(self):
        self.install()
        (self.destination / vendor.MODEL_DIRECTORY / "model.step").unlink()
        with self.assertRaisesRegex(vendor.VendorError, "missing model"):
            self.install()

    def test_existing_unversioned_snapshot_requires_base(self):
        self.destination.mkdir(parents=True)
        (self.destination / vendor.SYMBOL_LIBRARY).write_text(
            library(symbol("A", "base")))
        with self.assertRaisesRegex(vendor.VendorError, "base-ref"):
            self.install()


if __name__ == "__main__":
    unittest.main()
