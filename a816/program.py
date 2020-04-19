import logging
from a816.parse.mzparser import MZParser
from a816.parse.nodes import CodePositionNode, LabelNode, SymbolNode, BinaryNode, RelocationAddressNode, IncludeIpsNode
from a816.symbols import Resolver
from a816.writers import IPSWriter, SFCWriter
from a816.parse.nodes import NodeError
from a816.cpu.cpu_65c816 import RomType

logger = logging.getLogger('a816')


class Program(object):
    def __init__(self, parser=None, dump_symbols=False):
        self.resolver = Resolver()
        self.logger = logging.getLogger('x816')
        self.dump_symbols = dump_symbols
        self.parser = parser or MZParser(self.resolver)

    def resolver_reset(self):
        self.resolver.pc = 0x000000
        self.resolver.last_used_scope = 0
        self.resolver.current_scope = self.resolver.scopes[0]

    def resolve_labels(self, program_nodes):
        self.resolver.last_used_scope = 0

        previous_pc = self.resolver.reloc_address

        for node in program_nodes:
            if isinstance(node, SymbolNode):
                continue
            previous_pc = node.pc_after(previous_pc)

        self.resolver_reset()

        previous_pc = self.resolver.reloc_address
        for node in program_nodes:
            if isinstance(node, LabelNode) or isinstance(node, BinaryNode):
                continue
            previous_pc = node.pc_after(previous_pc)
        self.resolver_reset()

    def emit(self, program, writer):
        current_block = b''
        current_block_addr = self.resolver.pc
        for node in program:
            node_bytes = node.emit(self.resolver)

            if node_bytes:
                current_block += node_bytes
                self.resolver.pc += len(node_bytes)
                self.resolver.reloc_address += len(node_bytes)

            if isinstance(node, CodePositionNode):  # or isinstance(node, RelocationAddressNode):
                if len(current_block) > 0:
                    writer.write_block(current_block, current_block_addr)
                current_block_addr = self.resolver.pc
                current_block = b''

            if isinstance(node, IncludeIpsNode):
                for block_addr, block in node.blocks:
                    writer.write_block(block, block_addr)

        if len(current_block) > 0:
            writer.write_block(current_block, current_block_addr)

    def emit_ips(self, program, file):
        ips = IPSWriter(file)
        ips.begin()
        self.emit(program, ips)
        ips.end()

    def assemble_string_with_emitter(self, input_program, filename, emitter):
        nodes = self.parser.parse(input_program, filename)
        self.logger.info('Resolving labels')
        self.resolve_labels(nodes)

        if self.dump_symbols:
            self.resolver.dump_symbol_map()

        self.emit(nodes, emitter)

    def assemble_with_emitter(self, asm_file, emitter):
        try:
            with open(asm_file, encoding='utf-8') as f:
                input_program = f.read()
                try:
                    self.assemble_string_with_emitter(input_program, asm_file, emitter)
                except NodeError as e:
                    logger.error(str(e))

        except RuntimeError as e:
            self.logger.error(e)
            return -1

        self.logger.info('Success !')
        return 0

    def assemble(self, asm_file, sfc_file):
        with open(sfc_file, 'wb') as f:
            sfc_emiter = SFCWriter(f)
            self.assemble_with_emitter(asm_file, sfc_emiter)

    def assemble_as_patch(self, asm_file, ips_file, mapping=None, copier_header=False):
        if mapping is not None:
            address_mapping = {
                'low': RomType.low_rom,
                'low2': RomType.low_rom_2,
                'high': RomType.high_rom
            }
            self.resolver.rom_type = address_mapping[mapping]
        with open(ips_file, 'wb') as f:
            ips_emitter = IPSWriter(f, copier_header)
            ips_emitter.begin()
            self.assemble_with_emitter(asm_file, ips_emitter)
            ips_emitter.end()
