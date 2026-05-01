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
