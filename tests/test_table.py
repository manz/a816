import os
import unittest

from script import Table

this_dir = os.path.dirname(__file__)

table_path = os.path.join(this_dir, "ff4fr.tbl")


class TableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.table = Table(table_path)

    def test_table_can_be_initialized_later(self) -> None:
        table = Table()
        table.include(table_path)

        text = table.to_text(b"\x04\x00")
        self.assertEqual(text, "Cecil")

    def test_ignore(self) -> None:
        text = self.table.to_text(b"\x02\x03")
        self.assertEqual(text, "[space][0x3]")

    def test_multibyte(self) -> None:
        text = self.table.to_text(b"\x04\x01")
        self.assertEqual(text, "Cain")

        encoded_text = self.table.to_bytes("Cain")
        self.assertEqual(encoded_text, b"\x04\x01")

    def test_unknown(self) -> None:
        text = self.table.to_text(b"\x39")
        self.assertEqual(text, "[0x39]")

        encoded_text = self.table.to_bytes("[0x40]")
        self.assertEqual(encoded_text, b"\x40")

    def test_unknown_char_skipped(self) -> None:
        encoded_text = self.table.to_bytes("hellò")
        text = self.table.to_text(encoded_text)
        self.assertEqual(text, "hell")
