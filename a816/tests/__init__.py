class StubWriter(object):
    def __init__(self):
        self.data = []
        self.data_addresses = []

    def begin(self):
        # not needed by StubWriter
        pass

    def write_block(self, block, block_address):
        self.data_addresses.append(block_address)
        self.data.append(block)

    def end(self):
        # not needed by StubWriter
        pass
