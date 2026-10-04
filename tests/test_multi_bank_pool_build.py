"""A pool whose ranges sit in two banks, filled by two modules through `a816 build`.

Exercises the whole object-mode path: each module compiles to its own
`.o`, the linker unions the same-named pool, runs first-fit over the
merged ranges and patches every section. Assertions read the IPS the
build writes (physical LoROM offsets: `$01:FFF0` -> `0x00FFF0`,
`$02:8000` -> `0x010000`).
"""

from __future__ import annotations

import logging
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from a816.cli import cli_main
from tests.ips_helpers import parse_ips_records

_BANK01_TAIL = 0x00FFF0
_BANK02_HEAD = 0x010000

_TWO_BANK_POOL = ".pool slack {\n    range 0x01fff0 0x01ffff\n    range 0x028000 0x02800f\n}\n"


def _alloc(name: str, byte: int, count: int) -> str:
    body = ", ".join(f"0x{byte:02x}" for _ in range(count))
    return f".alloc {name} in slack {{\n    .db {body}\n}}\n"


def _module(doc: str, pool: str, *allocs: str) -> str:
    return f'"""{doc}"""\n{pool}' + "".join(allocs)


def _run_cli(args: list[str]) -> tuple[int, str]:
    """Run the `a816` CLI; return (exit code, stderr)."""
    stderr = StringIO()
    argv = ["a816", *args]
    with patch.object(sys, "argv", argv), patch.object(sys, "stderr", stderr), pytest.raises(SystemExit) as exc_info:
        cli_main()
    return int(exc_info.value.code or 0), stderr.getvalue()


def _write_modules(tmp_path: Path, mod_a: str, mod_b: str) -> tuple[Path, Path]:
    paths = (tmp_path / "mod_a.s", tmp_path / "mod_b.s")
    for path, source in zip(paths, (mod_a, mod_b), strict=True):
        path.write_text(source, encoding="utf-8")
    return paths


def _run_build(tmp_path: Path, mod_a: str, mod_b: str) -> tuple[int, str]:
    """Write main + two modules, run `a816 build`; return (exit code, stderr)."""
    _write_modules(tmp_path, mod_a, mod_b)
    main = tmp_path / "main.s"
    main.write_text('"""m"""\n.import "mod_a"\n.import "mod_b"\n', encoding="utf-8")
    return _run_cli(["build", str(main), "-o", str(tmp_path / "out.ips"), "--obj-dir", str(tmp_path / "obj")])


def _compile_and_link(tmp_path: Path, mod_a: str, mod_b: str) -> list[tuple[int, bytes]]:
    """Separate compilation: `a816 -c mod_a.s mod_b.s`, then `a816 mod_a.o mod_b.o`."""
    sources = _write_modules(tmp_path, mod_a, mod_b)
    compile_code, compile_stderr = _run_cli(["-c", *(str(s) for s in sources)])
    assert compile_code == 0, compile_stderr
    objects = [str(s.with_suffix(".o")) for s in sources]
    exit_code, stderr = _run_cli([*objects, "-o", str(tmp_path / "out.ips")])
    assert exit_code == 0, stderr
    return parse_ips_records((tmp_path / "out.ips").read_bytes())


def _patched(records: list[tuple[int, bytes]]) -> dict[int, int]:
    return {offset + i: byte for offset, payload in records for i, byte in enumerate(payload)}


def _span(image: dict[int, int], start: int, length: int) -> bytes:
    return bytes(image.get(addr, 0) for addr in range(start, start + length))


@pytest.fixture(scope="module")
def two_bank_image(tmp_path_factory: pytest.TempPathFactory) -> dict[int, int]:
    """fn_a (12 bytes, module A) + fn_b (10 bytes) + fn_c (4 bytes, module B), built once."""
    tmp_path = tmp_path_factory.mktemp("two_bank")
    mod_a = _module("a", _TWO_BANK_POOL, _alloc("fn_a", 0xA1, 12))
    mod_b = _module("b", _TWO_BANK_POOL, _alloc("fn_b", 0xB2, 10), _alloc("fn_c", 0xC3, 4))
    exit_code, stderr = _run_build(tmp_path, mod_a, mod_b)
    assert exit_code == 0, stderr
    return _patched(parse_ips_records((tmp_path / "out.ips").read_bytes()))


def test_first_module_alloc_lands_at_bank01_tail(two_bank_image: dict[int, int]) -> None:
    assert _span(two_bank_image, _BANK01_TAIL, 12) == b"\xa1" * 12


def test_alloc_too_big_for_bank01_leftover_moves_to_bank02(two_bank_image: dict[int, int]) -> None:
    assert _span(two_bank_image, _BANK02_HEAD, 10) == b"\xb2" * 10


def test_second_module_alloc_fills_bank01_leftover_first_fit(two_bank_image: dict[int, int]) -> None:
    assert _span(two_bank_image, _BANK01_TAIL + 12, 4) == b"\xc3" * 4


def test_build_patches_exactly_the_allocated_bytes(two_bank_image: dict[int, int]) -> None:
    assert len(two_bank_image) == 12 + 10 + 4


def test_unioned_ranges_fill_lowest_address_first_regardless_of_declaring_module(tmp_path: Path) -> None:
    """Module B declares only the bank $02 range, yet fn_b lands in module A's bank $01 range."""
    mod_a = _module("a", ".pool slack { range 0x01fff0 0x01ffff }\n", _alloc("fn_a", 0xA1, 4))
    mod_b = _module("b", ".pool slack { range 0x028000 0x02800f }\n", _alloc("fn_b", 0xB2, 4))
    image = _patched(_compile_and_link(tmp_path, mod_a, mod_b))
    assert _span(image, _BANK01_TAIL, 8) == b"\xa1" * 4 + b"\xb2" * 4


def test_alloc_larger_than_any_bank_range_fails_the_build(tmp_path: Path) -> None:
    mod_a = _module("a", _TWO_BANK_POOL, _alloc("fn_a", 0xA1, 20))
    mod_b = _module("b", _TWO_BANK_POOL)
    assert _run_build(tmp_path, mod_a, mod_b)[0] == 1


def _build_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture, mod_a: str, mod_b: str) -> str:
    """`a816 build` logs link failures (`Build failed: ...`) instead of printing them."""
    with caplog.at_level(logging.ERROR, logger="a816.module_builder"):
        _run_build(tmp_path, mod_a, mod_b)
    return caplog.text


def test_alloc_larger_than_any_bank_range_names_the_bank_rule(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    mod_a = _module("a", _TWO_BANK_POOL, _alloc("fn_a", 0xA1, 20))
    mod_b = _module("b", _TWO_BANK_POOL)
    log = _build_failure(tmp_path, caplog, mod_a, mod_b)
    assert "alloc 'fn_a' (20 bytes) does not fit in pool 'slack': larger than its largest range (16 bytes)" in log


def test_fragmented_pool_reports_fragmentation(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    mod_a = _module("a", _TWO_BANK_POOL, _alloc("fn_a", 0xA1, 12))
    mod_b = _module("b", _TWO_BANK_POOL, _alloc("fn_b", 0xB2, 12), _alloc("fn_c", 0xC3, 8))
    log = _build_failure(tmp_path, caplog, mod_a, mod_b)
    assert "8 bytes free in total but fragmented" in log
