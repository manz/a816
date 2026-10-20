"""BPS patches: a816's linear encoder, the applier, sealing.

Flips (github.com/Alcaro/Flips) is the oracle: the tests that run it skip
when `flips` is not on PATH. `fixtures/bps/flips_delta.bps` is a Flips
delta patch (every action kind) between the seeded ROMs of `bps_roms`, so
the applier is checked against Flips output without the binary.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from a816 import bps
from a816.bps import BpsError
from tests.bps_roms import SOURCE_SIZE, delta_target, source_rom

_FIXTURE = Path(__file__).parent / "fixtures" / "bps" / "flips_delta.bps"
_FLIPS = shutil.which("flips")
needs_flips = pytest.mark.skipif(_FLIPS is None, reason="flips is not on PATH")

# Inside the source, touching, overlapping (the later wins), past the source.
_BLOCKS = [
    (0x0100, b"\x11" * 0x20),
    (0x0120, b"\x22" * 0x10),
    (0x0400, b"\x33" * 0x40),
    (0x0420, b"\x44" * 0x08),
    (0xFFF0, b"\x55" * 0x20),
    (0x18000, b"\x66" * 0x10),
]
_TARGET_SIZE = 0x20000


def _expected(blocks: list[tuple[int, bytes]], target_size: int) -> bytes:
    """The source with `blocks` applied as an IPS would, zero padded."""
    image = bytearray(source_rom())
    image += bytes(target_size - len(image))
    for address, data in blocks:
        image[address : address + len(data)] = data
    return bytes(image)


def _unsealed(metadata: str = "", blocks: list[tuple[int, bytes]] = _BLOCKS) -> bytes:
    return bps.encode_linear(
        blocks,
        source_size=SOURCE_SIZE,
        source_crc32=zlib.crc32(source_rom()),
        target_size=_TARGET_SIZE,
        metadata=metadata,
    )


def _raw(source_size: int, target_size: int, actions: bytes, source: bytes = b"") -> bytes:
    """A hand-built patch with a valid trailer, for the malformed cases."""
    body = bps.MAGIC + bps.encode_number(source_size) + bps.encode_number(target_size) + bps.encode_number(0)
    body += actions + struct.pack("<II", zlib.crc32(source), 0)
    return body + struct.pack("<I", zlib.crc32(body))


def _error(patch: bytes, source: bytes, **kwargs: bool) -> BpsError:
    with pytest.raises(BpsError) as excinfo:
        bps.apply(patch, source, **kwargs)
    return excinfo.value


class TestNumbers:
    @pytest.mark.parametrize(
        ("value", "encoded"), [(0, b"\x80"), (0x7F, b"\xff"), (0x80, b"\x00\x80"), (0x407F, b"\x7f\xff")]
    )
    def test_encodes_like_beat(self, value: int, encoded: bytes) -> None:
        assert bps.encode_number(value) == encoded

    @pytest.mark.parametrize("value", [0, 1, 0x7F, 0x80, 0x4080, 0x200000, 0xFFFFFFFF])
    def test_round_trips(self, value: int) -> None:
        reader = bps._Reader(bps.encode_number(value), len(bps.encode_number(value)))
        assert reader.number() == value

    @pytest.mark.parametrize("offset", [0, 1, -1, 0x1234, -0x1234])
    def test_offsets_round_trip(self, offset: int) -> None:
        encoded = bps._encode_offset(offset)
        assert bps._Reader(encoded, len(encoded)).offset() == offset


class TestLinearPatch:
    def test_applies_to_the_blocks_over_the_source(self) -> None:
        patch = bps.seal(_unsealed(), source_rom())
        assert bps.apply(patch, source_rom()) == _expected(_BLOCKS, _TARGET_SIZE)

    def test_blocks_in_any_order_give_the_same_patch(self) -> None:
        shuffled = [_BLOCKS[4], _BLOCKS[0], _BLOCKS[5], _BLOCKS[1], _BLOCKS[2], _BLOCKS[3]]
        assert _unsealed(blocks=shuffled) == _unsealed()

    def test_a_later_block_wins_an_overlap(self) -> None:
        blocks = [(0x0420, b"\x44" * 0x08), (0x0400, b"\x33" * 0x40)]
        patch = bps.seal(_unsealed(blocks=blocks), source_rom())
        assert bps.apply(patch, source_rom())[0x0420] == 0x33

    def test_zeros_past_the_source_cost_bytes_not_their_length(self) -> None:
        assert len(_unsealed(blocks=[(0x1FFF0, b"\x77" * 0x10)])) < 0x40

    def test_a_single_zero_past_the_source(self) -> None:
        blocks = [(SOURCE_SIZE + 1, b"\x01")]
        patch = bps.encode_linear(
            blocks, source_size=SOURCE_SIZE, source_crc32=zlib.crc32(source_rom()), target_size=SOURCE_SIZE + 2
        )
        assert bps.apply(patch, source_rom(), unsealed=True)[SOURCE_SIZE:] == b"\x00\x01"

    def test_records_sizes_crcs_and_metadata(self) -> None:
        info = bps.info(_unsealed(metadata='<patch name="Fé"/>'))
        assert (info.source_size, info.target_size) == (SOURCE_SIZE, _TARGET_SIZE)
        assert info.source_crc32 == zlib.crc32(source_rom())
        assert info.metadata == '<patch name="Fé"/>'

    def test_is_unsealed(self) -> None:
        assert not bps.info(_unsealed()).sealed

    def test_a_block_past_the_target_is_refused(self) -> None:
        with pytest.raises(ValueError, match="past the target size"):
            bps.encode_linear([(0x10, b"\x01\x02")], source_size=0x10, source_crc32=0, target_size=0x11)


class TestSeal:
    def test_writes_the_target_crc(self) -> None:
        info = bps.info(bps.seal(_unsealed(), source_rom()))
        assert info.target_crc32 == zlib.crc32(_expected(_BLOCKS, _TARGET_SIZE))

    def test_is_idempotent(self) -> None:
        sealed = bps.seal(_unsealed(), source_rom())
        assert bps.seal(sealed, source_rom()) == sealed

    def test_an_unsealed_patch_fails_its_target_crc(self) -> None:
        assert "patched ROM's CRC32" in _error(_unsealed(), source_rom()).message

    def test_an_unsealed_patch_applies_when_asked(self) -> None:
        assert bps.apply(_unsealed(), source_rom(), unsealed=True) == _expected(_BLOCKS, _TARGET_SIZE)

    def test_a_wrong_rom_is_rejected(self) -> None:
        wrong = bytearray(source_rom())
        wrong[0] ^= 0xFF
        error = _error(_unsealed(), bytes(wrong), unsealed=True)
        assert error.code.code == "E0512"
        assert "CRC32" in error.message

    def test_a_headered_rom_is_named(self) -> None:
        error = _error(_unsealed(), bytes(bps.COPIER_HEADER) + source_rom(), unsealed=True)
        assert error.code.code == "E0512"
        assert "copier header" in error.message

    def test_a_short_rom_is_rejected_by_size(self) -> None:
        assert "expects 0x10000" in _error(_unsealed(), source_rom()[:-1], unsealed=True).message


class TestActions:
    def test_a_target_copy_clear_of_the_output_copies_a_slice(self) -> None:
        actions = bps._action(1, 4) + b"abcd" + bps._action(3, 2) + bps._encode_offset(0)
        assert bps.apply(_raw(0, 6, actions), b"", unsealed=True) == b"abcdab"


class TestMalformed:
    def test_not_a_patch(self) -> None:
        assert _error(b"PATCH" + bytes(20), b"").code.code == "E0511"

    def test_a_corrupt_patch_fails_its_crc(self) -> None:
        patch = bytearray(_unsealed())
        patch[8] ^= 0xFF
        assert "patch's CRC32" in _error(bytes(patch), source_rom(), unsealed=True).message

    def test_a_truncated_header(self) -> None:
        assert "truncated" in _error(_raw(4, 4, b"")[:6] + bytes(12), b"").message

    def test_a_target_copy_before_any_byte(self) -> None:
        actions = bps._action(3, 2) + bps._encode_offset(0)
        assert "TargetCopy" in _error(_raw(0, 2, actions), b"", unsealed=True).message

    def test_a_source_copy_outside_the_source(self) -> None:
        actions = bps._action(2, 2) + bps._encode_offset(3)
        assert "SourceCopy" in _error(_raw(4, 2, actions, b"abcd"), b"abcd", unsealed=True).message

    def test_a_write_past_the_target(self) -> None:
        actions = bps._action(1, 3) + b"xyz"
        assert "past its target size" in _error(_raw(0, 2, actions), b"", unsealed=True).message

    def test_a_patch_short_of_its_target(self) -> None:
        actions = bps._action(1, 1) + b"x"
        assert "its target size is 0x2" in _error(_raw(0, 2, actions), b"", unsealed=True).message

    def test_formats_with_its_code(self) -> None:
        assert "bps error[E0511]" in _error(b"nope", b"").format()


class TestFlipsDeltaFixture:
    def test_applies_every_action_kind(self) -> None:
        assert bps.apply(_FIXTURE.read_bytes(), source_rom()) == delta_target()

    def test_is_sealed(self) -> None:
        assert bps.info(_FIXTURE.read_bytes()).sealed


@needs_flips
class TestFlips:
    def _write(self, tmp_path: Path, patch: bytes, source: bytes) -> tuple[Path, Path]:
        (tmp_path / "a816.bps").write_bytes(patch)
        (tmp_path / "source.sfc").write_bytes(source)
        return tmp_path / "a816.bps", tmp_path / "source.sfc"

    def _flips(self, *args: Path | str) -> subprocess.CompletedProcess[str]:
        assert _FLIPS is not None
        return subprocess.run([_FLIPS, *map(str, args)], capture_output=True, text=True, check=False)

    def test_flips_applies_a_sealed_patch(self, tmp_path: Path) -> None:
        patch, source = self._write(tmp_path, bps.seal(_unsealed(), source_rom()), source_rom())
        result = self._flips("--apply", "--exact", patch, source, tmp_path / "out.sfc")
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "out.sfc").read_bytes() == _expected(_BLOCKS, _TARGET_SIZE)

    def test_flips_reads_the_header(self, tmp_path: Path) -> None:
        sealed = bps.seal(_unsealed(metadata='<patch name="Final Fantasy IV"/>'), source_rom())
        patch, _ = self._write(tmp_path, sealed, source_rom())
        output = self._flips("--info", patch).stdout
        assert f"Input ROM: {SOURCE_SIZE} bytes, CRC32 {zlib.crc32(source_rom()):08X}" in output
        assert f"Output ROM: {_TARGET_SIZE} bytes, CRC32 {zlib.crc32(_expected(_BLOCKS, _TARGET_SIZE)):08X}" in output
        assert '  <patch name="Final Fantasy IV"/>' in output

    def test_flips_rejects_a_wrong_rom(self, tmp_path: Path) -> None:
        wrong = bytes(b ^ 0xFF for b in source_rom())
        patch, source = self._write(tmp_path, bps.seal(_unsealed(), source_rom()), wrong)
        result = self._flips("--apply", "--exact", patch, source, tmp_path / "out.sfc")
        assert "not intended for this ROM" in result.stdout
