import struct


class IPSWriter(object):
    def __init__(self, file, copier_header=False, check_for_overlap=False):
        self.file = file
        self._regions = []
        self._check_for_overlap = check_for_overlap
        self._copier_header = copier_header

    def _check_overlap(self, start, end):
        for region in self._regions:
            if region[0] >= start >= region[1] or region[0] >= end >= region[1]:
                raise OverflowError(
                    'This region was already patched {:#08x}-{:#08x}, {:#08x}-{:#08x}'.format(start, end, region[0],
                                                                                              region[1]))

    def begin(self):
        self.file.write(b'PATCH')

    def write_block_header(self, block, block_address):
        if self._copier_header:
            block_address += 0x200
        self.file.write(struct.pack('>BH', block_address >> 16, block_address & 0xFFFF))
        self.file.write(struct.pack('>H', len(block)))

    def write_block(self, block, block_address):
        if self._check_for_overlap:
            start, end = block_address, block_address + len(block)
            self._check_overlap(start, end)
            self._regions.append([start, end])

        k = 0
        while block[k:]:
            slice_size = min(0xFFFF, len(block) - k)
            block_slice = block[k:k + slice_size]

            self.write_block_header(block_slice, block_address)
            self.file.write(block_slice)
            block_address += slice_size

            k += slice_size

    def end(self):
        self.file.write(b'EOF')


class SFCWriter(object):
    def __init__(self, file, copier_header=False):
        self.file = file
        self.copier_header = copier_header

    def begin(self):
        pass

    def write_block_header(self, block, block_address):
        pass

    def write_block(self, block, block_address):
        self.file.seek(block_address)
        self.file.write(block)

    def end(self):
        pass
