import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location(
    "add_component", ROOT / "tools" / "add_component.py")
ADD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADD)


def symbol(description="description"):
    return f'''(symbol "Part"
\t\t(property "Reference" "U")
\t\t(property "Value" "Wrong")
\t\t(property "Footprint" "Wrong:Footprint")
\t\t(property "LCSC Part #" "C123456")
\t\t(property "Datasheet" "")
\t\t(property "Description" "{description}")
\t\t(symbol "Part_1_1"
\t\t\t(pin passive line
\t\t\t\t(at 0 0 0)
\t\t\t\t(length 2.54)
\t\t\t\t(name "A" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "1" (effects (font (size 1.27 1.27))))
\t\t\t)
\t\t)
\t)'''


def footprint(name="FP"):
    return f'''(footprint "{name}"
\t(version 20240108)
\t(generator pcbnew)
\t(layer "F.Cu")
\t(property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
\t(property "Value" "Wrong" (at 0 0 0) (layer "F.Fab"))
)\n'''


class SymbolValidationTests(unittest.TestCase):
    def test_fixes_value_and_footprint(self):
        fixed, warnings = ADD.fix_symbol("Part", symbol(), "FP")
        properties = ADD.props(fixed)
        self.assertEqual(properties["Value"], "Part")
        self.assertEqual(properties["Footprint"], "Avionics_Feet:FP")
        self.assertEqual(len(warnings), 2)

    def test_quoted_parentheses_are_valid(self):
        fixed, _ = ADD.fix_symbol(
            "Part", symbol("uses (balanced-looking) text"), "FP")
        self.assertIn("balanced-looking", fixed)

    def test_property_values_are_escaped(self):
        fixed = ADD.set_prop(symbol(), "Description", 'a "quote" \\ path', True)
        self.assertEqual(ADD.props(fixed)["Description"], 'a "quote" \\ path')

    def test_missing_properties_are_inserted_inside_symbol(self):
        incomplete = symbol().replace(
            '\n\t\t(property "Datasheet" "")', '').replace(
            '\n\t\t(property "Description" "description")', '')
        fixed, _ = ADD.fix_symbol("Part", incomplete, "FP")
        self.assertEqual(ADD.props(fixed)["Datasheet"], "")
        self.assertEqual(ADD.props(fixed)["Description"], "")
        ADD.parse_form(fixed, ("symbol",))

    def test_duplicate_pin_numbers_fail(self):
        duplicate = symbol().replace(
            '\n\t\t)',
            '''
\t\t\t(pin passive line
\t\t\t\t(name "B" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "1" (effects (font (size 1.27 1.27))))
\t\t\t)
\t\t)''', 1)
        with self.assertRaisesRegex(ADD.ValidationError, "duplicate pin"):
            ADD.fix_symbol("Part", duplicate, "FP")


class FootprintValidationTests(unittest.TestCase):
    def test_fixes_properties_and_adds_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "FP.kicad_mod")
            path.write_text(footprint(), encoding="utf-8")
            fixed, warnings = ADD.fix_footprint(
                str(path), "FP", "${KIPRJMOD}/Models/model.step")
        self.assertIn('(property "Reference" "REF**"', fixed)
        self.assertIn('(property "Value" "FP"', fixed)
        self.assertIn('(model "${KIPRJMOD}/Models/model.step"', fixed)
        self.assertEqual(len(warnings), 2)

    def test_replaces_existing_model_path(self):
        text = footprint().replace(
            "\n)", '\n\t(model "old.step" (offset (xyz 0 0 0)))\n)')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "FP.kicad_mod")
            path.write_text(text, encoding="utf-8")
            fixed, _ = ADD.fix_footprint(str(path), "FP", "new.step")
        self.assertIn('(model "new.step"', fixed)
        self.assertNotIn('old.step', fixed)


class SafetyTests(unittest.TestCase):
    def test_check_only_cli_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            symbol_path = Path(tmp, "Part.sym")
            footprint_path = Path(tmp, "FP.kicad_mod")
            model_path = Path(tmp, "model.step")
            symbol_path.write_text(symbol(), encoding="utf-8")
            footprint_path.write_text(footprint(), encoding="utf-8")
            model_path.write_bytes(b"STEP")
            result = subprocess.run([
                sys.executable, str(ROOT / "tools" / "add_component.py"),
                "--symbol-file", str(symbol_path),
                "--footprint-file", str(footprint_path),
                "--model-file", str(model_path),
                "--check-only",
            ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no errors", result.stdout)

    def test_model_name_cannot_escape_models(self):
        for name in ("../outside.step", "/tmp/outside.step", "a/b.step",
                     "a\\b.step"):
            with self.subTest(name=name):
                with self.assertRaises(ADD.ValidationError):
                    ADD.validate_model_name(name)
        self.assertEqual(ADD.validate_model_name("model.step"), "model.step")

    def test_dirty_target_is_rejected_even_when_dirty_tree_is_allowed(self):
        old_root = ADD.ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(["git", "init", "-q", tmp], check=True)
                target = Path(tmp, "target")
                target.write_text("data", encoding="utf-8")
                subprocess.run(["git", "add", "target"], cwd=tmp, check=True)
                ADD.ROOT = tmp
                with self.assertRaisesRegex(ADD.ValidationError, "target files"):
                    ADD.check_git_state(["target"], allow_dirty=True)
        finally:
            ADD.ROOT = old_root


if __name__ == "__main__":
    unittest.main()
