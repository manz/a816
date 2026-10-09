import re

# `joker_regex` without its `^`: matched at an index (`pattern.match(text, pos)`
# doesn't anchor `^` there).
_JOKER_AT = re.compile(r"\[0x(?P<byte>[0-9a-fA-F]+)]")
# Past this many distinct first characters `to_bytes` walks an index (see `_refresh`).
_SCANNER_MAX_FIRST_CHARS = 256


type _Trie = dict[str, _Trie]
"""A key-character trie; the empty-string child marks a key ending there."""


def _trie_pattern(keys: list[str]) -> str:
    """A regex matching the longest of `keys` at a position, built as a prefix
    trie (`a(?:b(?:c)?)?` for a, ab, abc).

    A flat `abc|ab|a` alternation is tried one alternative at a time: a
    kanji table with thousands of keys tested thousands per character. The
    trie branches one character at a time, and its greedy optional tails
    backtrack to the longest key that ends.
    """
    trie: _Trie = {}
    for key in keys:
        node = trie
        for char in key:
            node = node.setdefault(char, {})
        node[""] = {}
    return _node_pattern(trie)


def _node_pattern(node: _Trie) -> str:
    branches = [re.escape(char) + _node_pattern(child) for char, child in sorted(node.items()) if char]
    if not branches:
        return ""
    body = branches[0] if len(branches) == 1 else f"(?:{'|'.join(branches)})"
    if "" in node:
        # A key ends here: the longer keys below are optional, tried first.
        return f"(?:{body})?"
    return body


class Table:
    table_line_regex = re.compile(r"(?P<byte>[0-9a-fA-F]+)(?::(?P<ignore>[0-9a-fA-F]+))?\s*=(?P<text>[^\n]+)")
    joker_regex = re.compile(r"^\[0x(?P<byte>[0-9a-fA-F]+)]")

    def __init__(self, path: str | None = None) -> None:
        self.lookup: dict[str, bytes] = {}
        self.inverted_lookup: dict[bytes, str | tuple[str, int]] = {}
        self.max_bytes_length = 0
        self.max_text_length = 0
        self._compiled: re.Pattern[str] = re.compile(".", re.DOTALL)
        self._lengths: tuple[int, ...] = ()
        self._use_scanner = True
        self._scanner_for: tuple[int, int] | None = None

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

    def _refresh(self) -> None:
        """Rebuild the encoders when keys were added since.

        `_compiled` is one pattern for the whole encoding step: a `[0xNN]`
        joker, else the table's longest key at that position, else any
        character, which is skipped. `_lengths` serves the index walk. Keys
        longer than `max_text_length` (set by `include`) are left out, as the
        old scan never tried them.
        """
        cache_key = (len(self.lookup), self.max_text_length)
        if self._scanner_for == cache_key:
            return
        keys = [k for k in self.lookup if 0 < len(k) <= self.max_text_length]
        key_group = f"(?P<key>{_trie_pattern(keys)})|" if keys else ""
        self._compiled = re.compile(rf"\[0x(?P<byte>[0-9a-fA-F]+)]|{key_group}.", re.DOTALL)
        self._lengths = tuple(sorted({len(k) for k in keys}, reverse=True))
        # The regex engine tries the trie's first characters one by one: past a
        # few hundred (BL's 733-kanji jp.tbl) the index walk is faster.
        self._use_scanner = len({k[0] for k in keys}) <= _SCANNER_MAX_FIRST_CHARS
        self._scanner_for = cache_key

    def to_bytes(self, text: str) -> bytes:
        """Encode `text`: `[0xNN]` is that byte, else the longest table key at
        that position, else the character is skipped.

        Slicing the rest of the text at every step, and trying every length up
        to the longest key through a KeyError each, was 75% of BL's dialog
        reflow. Most tables now scan in the regex engine (`_scanner`); one with
        hundreds of first characters walks an index instead.
        """
        self._refresh()
        if not self._use_scanner:
            return self._walk_to_bytes(text)
        binary_text = bytearray()
        lookup = self.lookup
        for token in self._compiled.finditer(text):
            if token.lastgroup == "key":
                binary_text += lookup[token.group("key")]
            elif token.lastgroup == "byte":
                binary_text.append(int(token.group("byte"), 16))
        return bytes(binary_text)

    def _walk_to_bytes(self, text: str) -> bytes:
        """`to_bytes` one position at a time, trying the table's key lengths."""
        binary_text = bytearray()
        lookup = self.lookup
        position, end = 0, len(text)
        while position < end:
            joker = _JOKER_AT.match(text, position)
            if joker:
                binary_text.append(int(joker.group("byte"), 16))
                position = joker.end()
                continue
            for length in self._lengths:
                decoded = lookup.get(text[position : position + length]) if position + length <= end else None
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
