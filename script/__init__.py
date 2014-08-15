import re


class Table(object):
    table_line_regex = re.compile(r'(?P<byte>[0-9a-fA-F]+)(?::(?P<ignore>[0-9a-fA-F]+))?\s*=(?P<text>[^\n]+)')
    joker_regex = re.compile(r'^\[0x(?P<byte>[0-9a-fA-F]+)\]')


    def __init__(self, path):
        f = open(path, 'rt', encoding='utf-8')
        self.lookup = {}
        self.inverted_lookup = {}

        for line in f.readlines():
            matches = self.table_line_regex.match(line)
            if matches:
                byte = list(map(lambda b: int(''.join(b), 16), zip(*[iter(matches.group('byte'))] * 2)))
                text = matches.group('text')
                text = text.replace('\\n', '\n')
                self.lookup[text] = bytes(byte)

                if matches.group('ignore'):
                    self.inverted_lookup[bytes(byte)] = (text, int(matches.group('ignore')))
                else:
                    self.inverted_lookup[bytes(byte)] = text

        self.max_bytes_length = len(max(self.lookup.values(), key=len))
        self.max_text_length = len(max(self.lookup.keys(), key=len))

    def to_bytes(self, text):
        binary_text = []
        current_position = 0
        while text[current_position:]:
            remainder = text[current_position:]

            matches = self.joker_regex.match(remainder)

            if matches:
                binary_text += bytes([int(matches.group('byte'), 16)])
                current_position += 5
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
            for i in range(min(len(bytes), self.max_bytes_length), 0, -1):
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