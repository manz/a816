import logging
from a816.parse.lalrparser import LALRParser
from a816.parse.nodes import CodePositionNode, LabelNode, SymbolNode, BinaryNode
from a816.symbols import Resolver
from a816.writers import IPSWriter, SFCWriter


class Program(object):
    def __init__(self, parser=None):
        self.resolver = Resolver()
        self.logger = logging.getLogger('x816')

        self.parser = parser or LALRParser(self.resolver)

    def resolver_reset(self):
        self.resolver.pc = 0x000000
        self.resolver.last_used_scope = 0
        self.resolver.current_scope = self.resolver.scopes[0]

    def resolve_labels(self, program_nodes):
        self.resolver.last_used_scope = 0

        previous_pc = self.resolver.pc

        for node in program_nodes:
            # if isinstance(node, SymbolNode):
            #     continue
            previous_pc = node.pc_after(previous_pc)

        self.resolver_reset()

        previous_pc = self.resolver.pc
        for node in program_nodes:
            if isinstance(node, LabelNode) or isinstance(node, BinaryNode):
                continue
            previous_pc += node.pc_after(previous_pc)
        self.resolver_reset()

    def emit(self, program, writer):
        current_block = b''
        current_block_addr = self.resolver.pc
        for node in program:
            node_bytes = node.emit(self.resolver)

            if node_bytes:
                current_block += node_bytes
                self.resolver.pc += len(node_bytes)

            if isinstance(node, CodePositionNode):
                if len(current_block) > 0:
                    writer.write_block(current_block, current_block_addr)
                current_block_addr = self.resolver.pc
                current_block = b''

        if len(current_block) > 0:
            writer.write_block(current_block, current_block_addr)

        # blocks = sorted(blocks, key=lambda x: x[0])
        #
        # current_addr = None
        # for block in blocks:
        #     if current_addr:
        #         if current_addr > block[0]:
        #             raise Exception('overlapping blocks')
        #         else:
        #             current_addr = block[0] + block[1]

    def emit_ips(self, program, file):
        ips = IPSWriter(file)
        ips.begin()
        self.emit(program, ips)
        ips.end()

    def assemble_with_emitter(self, asm_file, emiter):
        try:
            with open(asm_file, encoding='utf-8') as f:
                input_program = f.read()
                nodes = self.parser.parse(input_program)
                self.logger.info('Resolving labels')
                self.resolve_labels(nodes)
            self.resolver.dump_symbol_map()

            self.emit(nodes, emiter)

        except RuntimeError as e:
            self.logger.error(e)
            return -1

        self.logger.info('Success !')
        return 0

    def assemble(self, asm_file, sfc_file):
        with open(sfc_file, 'wb') as f:
            sfc_emiter = SFCWriter(f)
            self.assemble_with_emitter(asm_file, sfc_emiter)

    def assemble_as_patch(self, asm_file, ips_file):
        with open(ips_file, 'wb') as f:
            ips_emiter = IPSWriter(f)
            self.assemble_with_emitter(asm_file, ips_emiter)
