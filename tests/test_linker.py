from a816.linker import Linker
from a816.object_file import ObjectFile, RelocationType, SymbolSection, SymbolType


def test_link() -> None:
    # Create some example object files
    obj1 = ObjectFile(
        b"\x01\x02\x00\x00",
        [
            ("global_sym", 0x01, SymbolType.GLOBAL, SymbolSection.CODE),
            ("local_sym", 0x00, SymbolType.LOCAL, SymbolSection.CODE),
        ],
        [(0x02, "global_sym", RelocationType.ABSOLUTE_16)],
    )

    obj2 = ObjectFile(
        b"\x00\x00\x05\x06",
        [
            ("global_sym2", 0x01, SymbolType.GLOBAL, SymbolSection.CODE),
            ("local_sym2", 0x00, SymbolType.LOCAL, SymbolSection.CODE),
        ],
        [(0x00, "global_sym", RelocationType.RELATIVE_16)],
    )

    # Create a linker instance
    linker = Linker([obj1, obj2])

    # Link the object files
    linked_obj = linker.link()

    # Write the linked object file to disk
    linked_obj.write("linked.o")

    # You can now read the linked object file to check the result
    linked_obj_read = ObjectFile.from_file("linked.o")

    print(f"Linked code: {linked_obj_read.code!r}")
    print(f"Linked symbols: {linked_obj_read.symbols}")
    print(f"Linked relocations: {linked_obj_read.relocations}")


def test_abs_label_keeps_address_through_relocation() -> None:
    """`.label`-declared names land in objects as `SymbolSection.ABS_LABEL`.
    The linker must NOT add the module's relocation delta to them — the
    user picked the address, so it stays absolute regardless of where the
    module ends up."""
    obj = ObjectFile(
        b"\x01\x02\x00\x00",
        [
            ("real_label", 0x00, SymbolType.GLOBAL, SymbolSection.CODE),
            ("mult8_far", 0x02855C, SymbolType.GLOBAL, SymbolSection.ABS_LABEL),
        ],
    )
    # Place the module at 0x010000 so any unwanted delta would be visible.
    linker = Linker([obj], base_address=0x010000)
    linker.link()

    by_name = {name: (addr, section) for name, addr, _typ, section in linker.linked_symbols}
    real_addr, real_section = by_name["real_label"]
    abs_addr, abs_section = by_name["mult8_far"]

    # CODE symbol picks up the linker's delta.
    assert real_section == SymbolSection.CODE
    assert real_addr == 0x010000
    # ABS_LABEL symbol stays where the user pinned it.
    assert abs_section == SymbolSection.ABS_LABEL
    assert abs_addr == 0x02855C
