import struct


class IPSWriter(object):
    def __init__(self, file):
        self.file = file

    def begin(self):
        self.file.write(b'PATCH')

    def write_block_header(self, block, block_address):
        self.file.write(struct.pack('>BH', block_address >> 16, block_address & 0xFFFF))
        self.file.write(struct.pack('>H', len(block)))

    def write_block(self, block, block_address):
        k = 0
        while block[k:]:
            slice_size = min(0xFFFF, len(block) - k)
            block_slice = block[k:k+slice_size]

            self.write_block_header(block_slice, block_address)
            self.file.write(block_slice)
            k += slice_size

    def end(self):
        self.file.write(b'EOF')