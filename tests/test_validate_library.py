import os
import sys
import unittest


TOOLS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools")
sys.path.insert(0, TOOLS)
import validate_library


class ValidateLibraryTests(unittest.TestCase):
    def test_breakouts_do_not_require_lcsc_part_numbers(self):
        self.assertTrue(validate_library.lcsc_is_optional("Sensor_Breakout"))

    def test_known_assembled_boards_do_not_require_lcsc_part_numbers(self):
        for name in ("SMT32F411_Blackpill", "kx13x", "neopixel"):
            self.assertTrue(validate_library.lcsc_is_optional(name))

    def test_components_require_lcsc_part_numbers(self):
        self.assertFalse(validate_library.lcsc_is_optional("MS5611"))


if __name__ == "__main__":
    unittest.main()
