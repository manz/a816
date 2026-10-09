import re

# `joker_regex` without its `^`: matched at an index (`pattern.match(text, pos)`
# doesn't anchor `^` there).
_JOKER_AT = re.compile(r"\[0x(?P<byte>[0-9a-fA-F]+)]")


class Table:
    table_line_regex = re.compile(r"(?P<byte>[0-9a-fA-F]+)(?::(?P<ignore>[0-9a-fA-F]+))?\s*=(?P<text>[^\n]+)")
    joker_regex = re.compile(r"^\[0x(?P<byte>[0-9a-fA-F]+)]")

    def __init__(self, path: str | None = None) -> None:
        self.lookup: dict[str, bytes] = {}
        self.inverted_lookup: dict[bytes, str | tuple[str, int]] = {}
        self.max_bytes_length = 0
        self.max_text_length = 0
        self._lengths: tuple[int, ...] = ()
        self._lengths_for: tuple[int, int] | None = None

        if path is not None:
            self.include(path)

    def include(self, path: str) -> None:
        """Includes a table content to the current instance."""
        with open(path, encoding="utf-8") as f:
            for line in f:
                self.parse_table_line(line)

            self.max_bytes_length = len(max(self.lookup.values(), key=len))
            self.max_text_length = len(max(self.lookup.keys(), key=len))

    def parse_table_line(self, line: str) -> bool:
        matches = self.table_line_regex.match(line)
        if matches:
            bytes_value = matches.group("byte")
            byte = self.transform_byte_matches_to_int(bytes_value)
            text = matches.group("text")
            text = text.replace("\\n", "\n")
            self.add_lookup(text, byte)

            if matches.group("ignore"):
                self.add_inverted_lookup(byte, text, int(matches.group("ignore")))
            else:
                self.add_inverted_lookup(byte, text)
        return matches is not None

    @staticmethod
    def transform_byte_matches_to_int(value: str) -> list[int]:
        return [int("".join(b), 16) for b in zip(*[iter(value)] * 2, strict=True)]

    def add_inverted_lookup(self, byte: list[int], text: str, ignore: int | None = None) -> None:
        if ignore is not None:
            self.inverted_lookup[bytes(byte)] = (text, ignore)
        else:
            self.inverted_lookup[bytes(byte)] = text

    def add_lookup(self, text: str, byte: list[int]) -> None:
        self.lookup[text] = bytes(byte)

    def _key_lengths(self) -> tuple[int, ...]:
        """The lengths the lookup's keys have, longest first, capped at
        `max_text_length` (set by `include`) as the old scan was. Recomputed
        when keys are added."""
        cache_key = (len(self.lookup), self.max_text_length)
        if self._lengths_for != cache_key:
            lengths = {len(key) for key in self.lookup if len(key) <= self.max_text_length}
            self._lengths = tuple(sorted(lengths, reverse=True))
            self._lengths_for = cache_key
        return self._lengths

    def to_bytes(self, text: str) -> bytes:
        """Encode `text`: `[0xNN]` is that byte, else the longest table key at
        that position, else the character is skipped.

        Walks an index: slicing the rest of the text at every step made a line
        quadratic in its length, and it tried every length up to the longest
        key through a KeyError each (75% of BL's dialog reflow).
        """
        binary_text = bytearray()
        lookup = self.lookup
        lengths = self._key_lengths()
        position, end = 0, len(text)
        while position < end:
            joker = _JOKER_AT.match(text, position)
            if joker:
                binary_text.append(int(joker.group("byte"), 16))
                position = joker.end()
                continue
            for length in lengths:
                if position + length <= end:
                    decoded = lookup.get(text[position : position + length])
                    if decoded is not None:
                        binary_text += decoded
                        position += length
                        break
            else:
                position += 1
        return bytes(binary_text)

    def to_text(self, binary: bytes) -> str:
        text = ""
        current_position = 0

        while current_position < len(binary):
            remainder = binary[current_position:]
            for i in range(min(len(remainder), self.max_bytes_length), 0, -1):
                lookup_bytes = remainder[:i]

                try:
                    decoded = self.inverted_lookup[lookup_bytes]
                    if isinstance(decoded, tuple):
                        current_position += i
                        text += decoded[0]
                        for _ in range(decoded[1]):
                            text += f"[{hex(binary[current_position])}]"
                            current_position += 1
                    else:
                        current_position += i
                        text += decoded
                    break
                except KeyError:
                    pass
            else:
                text += f"[{hex(binary[current_position])}]"
                current_position += 1

        return text
