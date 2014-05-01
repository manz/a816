import re

label_regexp = re.compile(r'^\s*(\w+):\s*$')
operand_size_regexp = re.compile(r'\.(al|as|xl|xs)\s*$')

opcode_regexp = r'^(?P<opcode>\w+)(?:\.(?P<size>[Bb]|[Ww]|[Ll]))?'
operand = r'(?:(?:(?:\$|0x)(?P<value>[0-9-A-Fa-f]+))|(?P<symbol>\w+))'
indexed = r'\s*,\s*(?P<index>[xXyY])'

immediate_regexp = opcode_regexp + r'\s+(?P<immediate>#)' + operand
direct_regexp = opcode_regexp + r'\s+' + operand
direct_indexed_regexp = opcode_regexp + r'\s+' + operand + indexed
indirect_regexp = opcode_regexp + r'\s+' + r'\[\s*' + operand + r'\s*\]'
indirect_indexed_regexp = indirect_regexp + indexed

symbol_define = re.compile(r'\.define\s+(?P<symbol_name>[_a-zA-Z][_a-zA-Z0-9]*)\s*='+operand)
