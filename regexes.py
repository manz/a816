import re

pc_change_regexp = r'^\*=\s*(?:(?:(?:\$|0x)(?P<value>[0-9-A-Fa-f]+)))$'
rom_type_regexp = r'^\.(?P<romtype>low_rom|low_rom_2|high_rom)$'

symbol_regex = r'[_a-zA-Z][_a-zA-Z0-9]*'
label_regexp = r'^(?P<label>' + symbol_regex + r')\s*:$'
operand_size_regexp = re.compile(r'\.(al|as|xl|xs)\s*$')

opcode_regexp = r'^(?P<opcode>\w+)(?:\.(?P<size>[Bb]|[Ww]|[Ll]))?'
operand = r'(?:(?:(?:\$|0x)(?P<value>[0-9-A-Fa-f]+))|(?P<symbol>{symbol}))'.format(symbol=symbol_regex)
indexed = r'\s*,\s*(?P<index>[xXyY])'

none_regexp = opcode_regexp
immediate_regexp = opcode_regexp + r'\s+(?P<immediate>#)' + operand
direct_regexp = opcode_regexp + r'\s+' + operand
direct_indexed_regexp = opcode_regexp + r'\s+' + operand + indexed
indirect_regexp = opcode_regexp + r'\s+' + r'\(\s*' + operand + r'\s*\)'
indirect_indexed_regexp = indirect_regexp + indexed

indirect_long_regexp = opcode_regexp + r'\s+' + r'\[\s*' + operand + r'\s*\]'
indirect_indexed_long_regexp = indirect_long_regexp + indexed

symbol_define = re.compile(r'\.define\s+(?P<symbol_name>[_a-zA-Z][_a-zA-Z0-9]*)\s*=' + operand)
