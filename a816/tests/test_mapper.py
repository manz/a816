import unittest

from cpu.cpu_65c816 import read_only_memory, RomMode, Mode20Mapper, Mode21Mapper, Mode25Mapper


class MapperTest(unittest.TestCase):
    def setUp(self):
        self.mode20 = Mode20Mapper()
        self.mode21 = Mode21Mapper()
        self.mode25 = Mode25Mapper()

    def test_mode_20(self):
        address = 0x208012
        mapped = self.mode20.map(address)
        unmapped = self.mode20.unmap(mapped)

        self.assertEquals(hex(address), hex(unmapped))

    def test_mode_21(self):
        address = 0xE14356
        mapped = self.mode21.map(address)
        unmapped = self.mode21.unmap(mapped)

        self.assertEquals(hex(address), hex(unmapped))

    def test_mode_25(self):
        address = 0x808000
        print(hex(self.mode25.map(address)))

