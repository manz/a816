from xml.etree import ElementTree
from a816.cpu.cpu_65c816 import snes_to_rom
from script import Table
from script.formulas import base_relative_16bits_pointer_formula, long_low_rom_pointer


class Pointer(object):
    def __init__(self, id, address=None):
        self.id = id
        self.address = address
        self.value = None


class Script(object):
    def __init__(self, rom):
        self.rom = rom

    def read_fixed_text_list(self, pointer_file, address, count, bytes_length):
        pointers = []
        pointer_file.seek(address)
        for i in range(count):
            ptr_data = pointer_file.read(bytes_length)
            pointer = Pointer(i)
            pointer.value = ptr_data
            pointers.append(pointer)

        return pointers

    def read_pointers(self, pointer_file, address, count, length, formula):
        pointer_file.seek(address)
        temporary_pointers = []
        for i in range(count):
            ptr_data = pointer_file.read(length)
            pointer = Pointer(i, formula(ptr_data))
            temporary_pointers.append(pointer)
        return temporary_pointers

    def read_pointers_content(self, pointers_to_dump, end_of_script_address):
        pointers = sorted(pointers_to_dump, key=lambda x: x.address)

        for index, pointer in enumerate(pointers):
            if index + 1 < len(pointers):
                next_pointer = pointers[index + 1]
                self.rom.seek(pointer.address)
                pointer.value = self.rom.read(next_pointer.address - pointer.address)

        last_pointer = pointers[-1]
        print(hex(end_of_script_address) + ' ' + hex(last_pointer.address))
        last_pointer.value = self.rom.read(end_of_script_address - last_pointer.address)

        return pointers

    def append_pointers(self, pointer_table_1, pointer_table_2):
        pointers_1 = sorted(pointer_table_1, key=lambda x: x.id)
        pointers_2 = sorted(pointer_table_2, key=lambda x: x.id)
        last_id = pointers_1[-1].id

        for pointer in pointers_2:
            pointer.id += last_id

        return pointers_1 + pointers_2


def write_pointers_as_xml(pointers, table, output_file):
    sorted_pointers_by_id = sorted(pointers, key=lambda p: p.id)
    with open(output_file, 'wt', encoding='utf-8') as fd:
        fd.writelines('<?xml version="1.0" encoding="utf-8"?>\n')
        fd.write('<sn:script xmlns:sn="http://snes.ninja/ScriptNS">\n')
        for pointer in sorted_pointers_by_id:
            fd.write('<sn:pointer id="{:d}">'.format(pointer.id))
            fd.write(table.to_text(pointer.value))
            fd.write('</sn:pointer>\n\n')
        fd.write('</sn:script>\n')


def write_pointers_value_as_binary(pointers, output_file):
    sorted_pointers = sorted(pointers, key=lambda x: x.id)
    with open(output_file, 'wb') as fd:
        for pointer in sorted_pointers:
            fd.write(pointer.value)


def write_pointers_addresses_as_binary(pointers, formula, output_file):
    sorted_pointers = sorted(pointers, key=lambda x: x.id)
    current_position = 0
    with open(output_file, 'wb') as fd:
        for pointer in sorted_pointers:
            fd.write(formula(current_position))
            current_position += len(pointer.value)


def read_pointers_from_xml(input_file, table, formatter=None):
    pointer_table = []
    with open(input_file, encoding='utf-8') as datasource:
        tree = ElementTree.parse(datasource)
        root = tree.getroot()

        for child in root:
            pointer_id = int(child.get('id'), 10) - 1
            text = child.text
            pointer = Pointer(pointer_id)
            pointer.value = table.to_bytes(formatter(text) if formatter else text)
            pointer_table.append(pointer)
    return pointer_table


def recode_pointer_values(pointers, from_table, to_table):
    for pointer in pointers:
        pointer.value = to_table.to_bytes(from_table.to_text(pointer.value))
