from collections.abc import Callable
from typing import BinaryIO, Protocol
from xml.etree import ElementTree

from script import Table


class FormulaProtocol(Protocol):
    def __call__(self, value: bytes) -> int:
        """Formula function protocol."""


class Pointer:
    def __init__(self, id: int, address: int | None = None) -> None:
        self.id = id
        self.address: int | None = address
        self.value: bytes | None = None

    def get_address(self) -> int:
        assert self.address is not None
        return self.address

    def get_value(self) -> bytes:
        assert self.value is not None
        return self.value


class Script:
    def __init__(self, rom: BinaryIO) -> None:
        self.rom = rom

    def read_fixed_text_list(
        self, pointer_file: BinaryIO, address: int, count: int, bytes_length: int
    ) -> list[Pointer]:
        pointers = []
        pointer_file.seek(address)
        for i in range(count):
            ptr_data = pointer_file.read(bytes_length)
            pointer = Pointer(i)
            pointer.value = ptr_data
            pointers.append(pointer)

        return pointers

    def read_pointers(
        self, pointer_file: BinaryIO, address: int, count: int, length: int, formula: FormulaProtocol
    ) -> list[Pointer]:
        pointer_file.seek(address)
        temporary_pointers = []
        for i in range(count):
            ptr_data = pointer_file.read(length)
            pointer = Pointer(i, formula(ptr_data))
            temporary_pointers.append(pointer)
        return temporary_pointers

    def read_pointers_content(self, pointers_to_dump: list[Pointer], end_of_script_address: int) -> list[Pointer]:
        def sort_func(ptr: Pointer) -> int:
            return ptr.get_address()

        pointers = sorted(pointers_to_dump, key=sort_func)

        for index, pointer in enumerate(pointers):
            if index + 1 < len(pointers):
                next_pointer = pointers[index + 1]

                self.rom.seek(pointer.get_address())

                pointer.value = self.rom.read(next_pointer.get_address() - pointer.get_address())

        last_pointer = pointers[-1]

        print(hex(end_of_script_address) + " " + hex(last_pointer.get_address()))
        last_pointer.value = self.rom.read(end_of_script_address - last_pointer.get_address())

        return pointers

    def append_pointers(self, pointer_table_1: list[Pointer], pointer_table_2: list[Pointer]) -> list[Pointer]:
        pointers_1 = sorted(pointer_table_1, key=lambda x: x.id)
        pointers_2 = sorted(pointer_table_2, key=lambda x: x.id)
        last_id = pointers_1[-1].id

        for pointer in pointers_2:
            pointer.id += last_id

        return pointers_1 + pointers_2


def write_pointers_as_xml(pointers: list[Pointer], table: Table, output_file: str) -> None:
    sorted_pointers_by_id = sorted(pointers, key=lambda p: p.id)
    with open(output_file, "w", encoding="utf-8") as fd:
        fd.writelines('<?xml version="1.0" encoding="utf-8"?>\n')
        fd.write('<sn:script xmlns:sn="http://snes.ninja/ScriptNS">\n')
        for pointer in sorted_pointers_by_id:
            fd.write(f'<sn:pointer id="{pointer.id:d}">')
            fd.write(table.to_text(pointer.get_value()))
            fd.write("</sn:pointer>\n\n")
        fd.write("</sn:script>\n")


def write_pointers_value_as_binary(pointers: list[Pointer], output_file: str) -> None:
    sorted_pointers = sorted(pointers, key=lambda x: x.id)
    with open(output_file, "wb") as fd:
        for pointer in sorted_pointers:
            fd.write(pointer.get_value())


def write_pointers_addresses_as_binary(
    pointers: list[Pointer], formula: Callable[[int], bytes], output_file: str
) -> None:
    sorted_pointers = sorted(pointers, key=lambda x: x.id)
    current_position = 0
    with open(output_file, "wb") as fd:
        for pointer in sorted_pointers:
            fd.write(formula(current_position))
            current_position += len(pointer.get_value())


def read_pointers_from_xml(
    input_file: str, table: Table, formatter: Callable[[str], str] | None = None
) -> list[Pointer]:
    pointer_table = []
    with open(input_file, encoding="utf-8") as data_source:
        tree = ElementTree.parse(data_source)
        root = tree.getroot()

        for child in root:
            id_str = child.get("id")
            assert id_str is not None
            pointer_id = int(id_str, 10) - 1
            text = child.text
            pointer = Pointer(pointer_id)
            assert text is not None
            pointer.value = table.to_bytes(formatter(text) if formatter else text)
            pointer_table.append(pointer)
    return pointer_table


def recode_pointer_values(pointers: list[Pointer], from_table: Table, to_table: Table) -> None:
    for pointer in pointers:
        pointer.value = to_table.to_bytes(from_table.to_text(pointer.get_value()))
