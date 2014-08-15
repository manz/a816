import re

pc_change_regexp = r'^(?:(?:\*=\s*)|(?:\.patch \())(?:(?:(?:\$|0x)))(?P<value>[0-9-A-Fa-f]+)\)?$'

rom_type_regexp = r'^\.(?P<romtype>low_rom|low_rom_2|high_rom)$'

data_word_regexp = r'\.dw (?P<data>[^;]+)'
data_byte_regexp = r'\.db (?P<data>[^;]+)'

push_context_regexp = r'^\{'
pop_context_regexp = r'^\}'

symbol_regex = r'[_a-zA-Z][_a-zA-Z0-9]*'
label_regexp = r'^(?P<label>' + symbol_regex + r')\s*:$'
operand_size_regexp = re.compile(r'\.(al|as|xl|xs)\s*$')

include_binary_regex = r'\.incbin "(?P<path>[^"]+)"'
include_source_regex = r'\.incsrc "(?<path>[^"]+)"'

text_table_regexp = r'\.table "(?P<table>[^"]+)"'
text_regexp = r'\.text "(?P<text>[^"]+)"'

define_symbol_regex = r'^(?P<symbol>[_a-zA-Z][_a-zA-Z0-9]*)\s*=\s*(?P<expression>.*)$'

opcode_regexp = r'^(?P<opcode>\w+)(?:\.(?P<size>[Bb]|[Ww]|[Ll]))?'

operand = r'(?P<expression>[^\[\]\(\),]+)'

indexed = r'\s*,\s*(?P<index>[xXyYSs])'

pointer_regexp = r'\.pointer\s+' + operand

comment_regexp = r'\s*;.*'

none_regexp = opcode_regexp
immediate_regexp = opcode_regexp + r'\s+(?P<immediate>#)' + operand
direct_regexp = opcode_regexp + r'\s+' + operand
direct_indexed_regexp = opcode_regexp + r'\s+' + operand + indexed
indirect_regexp = opcode_regexp + r'\s+' + r'\(\s*' + operand + r'\s*\)'
indirect_indexed_regexp = indirect_regexp + indexed

indirect_long_regexp = opcode_regexp + r'\s+' + r'\[\s*' + operand + r'\s*\]'
indirect_indexed_long_regexp = indirect_long_regexp + indexed

