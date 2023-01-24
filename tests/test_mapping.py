import unittest

from a816.cpu.mapping import Bus

# mapping.yml
#   rom name=program.rom size=0x180000
#   ram name=save.ram size=0x2000
#   map id=rom address=00-6f,80-ff:8000-ffff mask=0x8000
#   map id=ram address=70-7d,f0-ff:0000-ffff


class MappingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = Bus()
        # map rom
        self.bus.map("1", (0x00, 0x6F), (0x8000, 0xFFFF), mask=0x8000, mirror_bank_range=(0x80, 0xCF))
        # map ram
        self.bus.map("2", (0x7E, 0x7F), (0, 0xFFFF), mask=0x10_000, writeable=True)

    def test_address_incrementation(self) -> None:
        addr = self.bus.get_address(0x12_FFFF) + 1
        self.assertEqual(addr.logical_value, 0x13_8000)
        self.assertEqual(addr.physical, 0x09_8000)

    def test_addresses_in_mirror_should_stay_there(self) -> None:
        mirror_addr = self.bus.get_address(0x80_8000)
        next_bank_addr = (mirror_addr + 0x8000).logical_value
        self.assertEqual(next_bank_addr, 0x81_8000)

    def test_unmap(self) -> None:
        self.bus.unmap("1")
        self.assertFalse("1" in self.bus.mappings, "Mapping with identifier 1 should have been removed.")
        self.assertTrue("2" in self.bus.mappings, "Mapping with identifier 2 should have been kept.")

    def test_unmap_not_editable_bus_should_raise(self) -> None:
        bus = Bus("test ro")
        bus.map("1", (0x00, 0x6F), (0x8000, 0xFFFF), mask=0x8000, mirror_bank_range=(0x80, 0xCF))
        bus.editable = False

        self.assertRaises(RuntimeError, bus.unmap, 1)

    def test_map_not_editable_bus_should_raise(self) -> None:
        bus = Bus("test ro")
        bus.editable = False

        with self.assertRaises(RuntimeError):
            bus.map("1", (0x00, 0x6F), (0x8000, 0xFFFF), mask=0x8000, mirror_bank_range=(0x80, 0xCF))

    def test_ram_should_be_writable(self) -> None:
        ram_address = self.bus.get_address(0x7E0000)
        self.assertTrue(ram_address.writable)

    def test_rom_should_not_be_writable(self) -> None:
        rom_address = self.bus.get_address(0x008000)
        self.assertFalse(rom_address.writable)

    def test_addresses_can_only_be_added_with_ints(self) -> None:
        rom_address = self.bus.get_address(0x008000)

        with self.assertRaises(ValueError):
            _ = rom_address + "coin"  # type: ignore
