import unittest

from script.formulas import base_relative_16bits_pointer_formula, long_low_rom_pointer


class FormulaTestCase(unittest.TestCase):
    def test_base_relative_pointer_formula(self) -> None:
        formula = base_relative_16bits_pointer_formula(0x123456)

        self.assertEqual(0x123456 + 0x2010, formula(b"\x10\x20"))

    def test_long_low_rom_pointer(self) -> None:
        converter = long_low_rom_pointer(0x10_0000)

        self.assertEqual(b"\x0c\x80\x20", converter(12))
