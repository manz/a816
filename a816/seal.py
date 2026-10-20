"""Sealing a patch against its base ROM: what only the patched ROM can give.

A BPS patch gets the patched ROM's CRC32; both formats get the SNES header
checksum when the project declares its cartridge (see `snes_header`).
"""

from __future__ import annotations

from a816 import bps, ips
from a816.error_codes import E_SEAL_COPIER_HEADER
from a816.ips import IpsError
from a816.snes_header import Cartridge, checksum_fields, with_checksum

COPIER_HEADER = 0x200


def _check_headerless(base: bytes) -> None:
    if len(base) % 0x8000 == COPIER_HEADER:
        raise IpsError(
            E_SEAL_COPIER_HEADER,
            f"the base ROM is {len(base):#x} bytes, 512 over a whole bank: strip its copier header first",
        )


def seal_bps(patch: bytes, source: bytes, cartridge: Cartridge | None) -> bytes:
    """`patch` sealed on `source`; with a cartridge, its target's header checksum fixed first.

    A wrong checksum means a different target, so the patch is encoded
    again (linear, from the differences with `source`), metadata kept.
    """
    if cartridge is None:
        return bps.seal(patch, source)
    target = bps.apply(patch, source, unsealed=True)
    fixed = with_checksum(target, cartridge)
    if fixed == target:
        return bps.seal(patch, source)
    return bps.diff(source, fixed, bps.info(patch).metadata)


def seal_ips(patch: bytes, base: bytes, cartridge: Cartridge) -> bytes:
    """`patch` ending with the header checksum record (and a pad to `rom_size` when it lacks one).

    Sealing again replaces that last record rather than adding one.
    """
    _check_headerless(base)
    rom = ips.apply(patch, base)
    if len(rom) < cartridge.rom_size:
        patch = ips.append_record(patch, cartridge.rom_size - 1, b"\x00")
    return ips.set_last_record(patch, cartridge.checksum_offset, checksum_fields(rom, cartridge))
