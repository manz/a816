import struct
import unittest
from io import BufferedReader, BytesIO
from typing import List, Tuple

from a816.writers import IPSWriter


class WriterTestCase(unittest.TestCase):
    def read_patch(self, data: memoryview) -> List[Tuple[int, int, bytes]]:
        patch = BufferedReader(BytesIO(bytes(data)))  # type:ignore
        patch.seek(5)
        blocks = []
        while patch.peek(3) != b"EOF":
            d = struct.unpack(">BH", patch.read(3))

            addr = d[0] << 16 | d[1]
            length = struct.unpack(">H", patch.read(2))
            block_data = patch.read(length[0])

            blocks.append((addr, length[0], block_data))
        return blocks

    def test_ips_writer(self) -> None:
        f = BytesIO()
        writer = IPSWriter(f)
        writer.begin()
        block_data = b"ea,pf,aep,pfeao,pof,eapo,e"
        writer.write_block(block_data, 0x8000)
        writer.end()

        data = f.getbuffer()
        blocks = self.read_patch(data)

        self.assertEqual([(0x8000, len(block_data), block_data)], blocks)
        self.assertEqual(b"PATCH", data[:5])
        self.assertEqual(b"EOF", data[-3:])

    def test_ips_writer_huge(self) -> None:
        """Should split blocks with size > 0xffff"""
        f = BytesIO()
        writer = IPSWriter(f)
        writer.begin()
        block_data = b"a" * 0x10_000
        writer.write_block(block_data, 0x8000)
        writer.end()

        data = f.getbuffer()
        blocks = self.read_patch(data)

        self.assertEqual(
            [
                (0x8000, 0xFFFF, block_data[:0xFFFF]),
                (0x8000 + 0xFFFF, 0x1, b"a"),
            ],
            blocks,
        )
