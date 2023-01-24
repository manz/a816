import logging
from pathlib import Path
from typing import List, Optional

from a816.cpu.cpu_65c816 import RomType
from a816.parse.mzparser import MZParser
from a816.parse.nodes import (
    BinaryNode,
    CodePositionNode,
    IncludeIpsNode,
    LabelNode,
    NodeError,
    NodeProtocol,
    SymbolNode,
)
from a816.symbols import Resolver
from a816.writers import IPSWriter, SFCWriter, Writer

logger = logging.getLogger("a816")


class Program:
    def __init__(self, parser: Optional[MZParser] = None, dump_symbols: bool = False):
        self.resolver = Resolver()
        self.logger = logging.getLogger("x816")
        self.dump_symbols = dump_symbols
        self.parser = parser or MZParser(self.resolver)

    def get_physical_address(self, logical_address: int) -> int:
        physical_address = self.resolver.get_bus().get_address(logical_address).physical
        if physical_address is not None:
            return physical_address
        else:
            raise RuntimeError(f"{logical_address} has no physical address.")

    def resolver_reset(self) -> None:
        """resets the resolver"""
        self.resolver.pc = 0x000000
        self.resolver.last_used_scope = 0
        self.resolver.current_scope = self.resolver.scopes[0]

    def resolve_labels(self, program_nodes: List[NodeProtocol]) -> None:
        """
        Resolves the labels
        :param program_nodes:
        :return:
        """
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

    def emit(self, program: List[NodeProtocol], writer: Writer) -> None:
        current_block = b""
        current_block_addr = self.resolver.pc
        for node in program:
            node_bytes = node.emit(self.resolver.reloc_address)

            if node_bytes:
                current_block += node_bytes
                self.resolver.pc += len(node_bytes)
                self.resolver.reloc_address += len(node_bytes)

            if isinstance(node, CodePositionNode):  # or isinstance(node, RelocationAddressNode):
                if len(current_block) > 0:
                    writer.write_block(current_block, current_block_addr)
                current_block_addr = self.resolver.pc
                current_block = b""

            if isinstance(node, IncludeIpsNode):
                for block_addr, block in node.blocks:
                    writer.write_block(block, block_addr)

        if len(current_block) > 0:
            writer.write_block(current_block, current_block_addr)

    def assemble_string_with_emitter(self, input_program: str, filename: str, emitter: Writer) -> None:
        nodes = self.parser.parse(input_program, filename)
        self.logger.info("Resolving labels")
        self.resolve_labels(nodes)

        if self.dump_symbols:
            self.resolver.dump_symbol_map()

        self.emit(nodes, emitter)

    def assemble_with_emitter(self, asm_file: str, emitter: Writer) -> int:
        try:
            with open(asm_file, encoding="utf-8") as f:
                input_program = f.read()
                try:
                    self.assemble_string_with_emitter(input_program, asm_file, emitter)
                except NodeError as e:
                    logger.error(str(e))

        except RuntimeError as e:
            self.logger.error(e)
            return -1

        self.logger.info("Success !")
        return 0

    def assemble(self, asm_file: str, sfc_file: Path) -> int:
        """
        Compile asmfile.
        :param asm_file:
        :param sfc_file:
        :return: error code
        """
        with open(sfc_file, "wb") as f:
            sfc_emitter = SFCWriter(f)
            return self.assemble_with_emitter(asm_file, sfc_emitter)

    def assemble_as_patch(
        self, asm_file: str, ips_file: Path, mapping: Optional[str] = None, copier_header: bool = False
    ) -> int:
        if mapping is not None:
            address_mapping = {"low": RomType.low_rom, "low2": RomType.low_rom_2, "high": RomType.high_rom}
            self.resolver.rom_type = address_mapping[mapping]
        with open(ips_file, "wb") as f:
            ips_emitter = IPSWriter(f, copier_header)
            ips_emitter.begin()
            exit_code = self.assemble_with_emitter(asm_file, ips_emitter)
            ips_emitter.end()
            return exit_code

    def exports_symbol_file(self, filename: str) -> None:
        """
        Exports the symbols into a file suited for bsnes sym debugger.
        :param filename:
        :return:
        """
        with open(filename, "wt", encoding="utf-8") as output_file:
            labels = self.resolver.get_all_labels()
            output_file.write("[labels]\n")
            for name, value in labels:
                bank = value >> 16 & 0xFF
                offset = value & 0xFFFF
                output_file.write(f"{bank:2x}:{offset:4x} {name}\n")
