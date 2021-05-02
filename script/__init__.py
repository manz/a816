import re


class Table(object):
    table_line_regex = re.compile(r'(?P<byte>[0-9a-fA-F]+)(?::(?P<ignore>[0-9a-fA-F]+))?\s*=(?P<text>[^\n]+)')
    joker_regex = re.compile(r'^\[0x(?P<byte>[0-9a-fA-F]+)\]')

    def __init__(self, path):
        self.lookup = {}
        self.inverted_lookup = {}
        self.max_bytes_length = 0
        self.max_text_length = 0
        self.include(path)

    @property
    def items(self):
        return self.lookup.items()
    
    
    def include(self, path):
        with open(path, 'rt', encoding='utf-8') as f:
            for line in f.readlines():
                self.parse_table_line(line)

            self.max_bytes_length = len(max(self.lookup.values(), key=len))
            self.max_text_length = len(max(self.lookup.keys(), key=len))

    def parse_table_line(self, line) -> bool:
        matches = self.table_line_regex.match(line)
        if matches:
            bytes_iterator = iter(matches.group('byte'))
            byte = self.transform_byte_matches_to_int(bytes_iterator)
            text = matches.group('text')
            text = text.replace('\\n', '\n')
            self.add_lookup(text, byte)

            if matches.group('ignore'):
                self.add_inverted_lookup(byte, text, int(matches.group('ignore')))
            else:
                self.add_inverted_lookup(byte, text)
        return matches is not None

    @staticmethod
    def transform_byte_matches_to_int(bytes_iterator):
        return list(map(lambda b: int(''.join(b), 16), zip(*[bytes_iterator] * 2)))

    def add_inverted_lookup(self, byte: list, text: str, ignore: int =None):
        if ignore is not None:
            self.inverted_lookup[bytes(byte)] = (text, ignore)
        else:
            self.inverted_lookup[bytes(byte)] = text

    def add_lookup(self, text, byte):

        self.lookup[text] = bytes(byte)

    def to_bytes(self, text):
        binary_text = []
        current_position = 0
        while text[current_position:]:
            remainder = text[current_position:]

            matches = self.joker_regex.match(remainder)

            if matches:
                binary_text += bytes([int(matches.group('byte'), 16)])
                current_position += len(matches.group())
                continue

            for i in range(min(len(text), self.max_text_length), 0, -1):
                lookup_text = remainder[:i]
                try:
                    decoded = self.lookup[lookup_text]
                    binary_text += decoded
                    current_position += i
                    break
                except KeyError:
                    pass
            else:
                current_position += 1

        return bytes(binary_text)

    def to_text(self, bytes):
        text = ''
        current_position = 0

        while current_position < len(bytes):
            remainder = bytes[current_position:]
            for i in range(min(len(remainder), self.max_bytes_length), 0, -1):
                lookup_bytes = remainder[:i]

                try:
                    decoded = self.inverted_lookup[lookup_bytes]
                    if isinstance(decoded, tuple):
                        current_position += i
                        text += decoded[0]
                        for k in range(decoded[i]):
                            text += '[' + hex(bytes[current_position]) + ']'
                            current_position += 1
                    else:
                        current_position += i
                        text += decoded
                    break
                except KeyError:
                    pass
            else:
                text += '[' + hex(bytes[current_position]) + ']'
                current_position += 1

        return text
