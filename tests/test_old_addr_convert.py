import unittest

from a816.cpu.cpu_65c816 import RomType, rom_to_snes, snes_to_rom


class OldAddrConvertTestCase(unittest.TestCase):
    def test_rom_to_snes(self) -> None:
        res = rom_to_snes(0x123456, RomType.low_rom)
        self.assertEqual(0x24B456, res)

        res = rom_to_snes(0x123456, RomType.low_rom_2)
        self.assertEqual(0xA4B456, res)

        res = rom_to_snes(0x123456, RomType.high_rom)
        self.assertEqual(0xD23456, res)

    def test_snes_to_rom(self) -> None:
        res = snes_to_rom(0x24B456)
        self.assertEqual(0x123456, res)

        res = snes_to_rom(0xA4B456)
        self.assertEqual(0x123456, res)

        res = snes_to_rom(0xD23456)
        self.assertEqual(0x123456, res)


if __name__ == "__main__":
    unittest.main()
