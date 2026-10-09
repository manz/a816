"""Seeded torture tests for pools: random multi-module projects, placement invariants checked.

Each seed generates a LoROM project and builds it three times from its own
directory with relative paths: cold, warm with nothing changed, and warm after
editing one module. Pool exhaustion (E0404 / E0405) is a legal outcome; anything else
that fails, or any broken invariant, is a bug and names its seed.

- ROM pools: 1-3 pools over 1-3 bank windows (pack / order), 1-5 modules,
  blobs with `align`, `cross_bank`, a pin inside a pool, `.reclaim`s in a
  header every module includes, and code that `jsl`s another module's alloc.
  It found the provisional-base E0317 (#255) and the lost and repeated
  `.reclaim` (#256) on rc1.
- bss pools: one WRAM pool with 1-3 contexts, sized, typed and pinned
  reservations over 1-4 modules, some made in the pool itself. It found the
  direct reservation that shared bytes with a context.

`A816_TORTURE_SEEDS=500 pytest tests/test_pool_torture.py` runs more seeds.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

SEEDS = range(int(os.environ.get("A816_TORTURE_SEEDS", "12")))
MAP = '[map.1]\naddress = "00-6f,80-cf:8000-ffff"\nmask = 0x8000\n'
EXHAUSTION = ("E0404", "E0405")


def _offset(addr: int) -> int:
    """LoROM file offset of a bus address."""
    return ((addr >> 16) & 0x7F) * 0x8000 + (addr & 0x7FFF)


@dataclass
class Built:
    ok: bool
    symbols: dict[str, int]
    rom: bytes
    log: str


def _build(caplog: pytest.LogCaptureFixture) -> Built:
    """Build the project in the working directory, with relative paths as a
    project's own build does (ff4's warm-build E0400 needed them)."""
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = build_with_imports(
            Path("src/main.s"),
            Path("out.sfc"),
            module_paths=[Path("src")],
            include_paths=[Path("src"), Path(".")],
            output_dir=Path("obj"),
            output_format="sfc",
        )
    ok = result.exit_code == 0
    rom = Path("out.sfc").read_bytes() if ok else b""
    return Built(ok, dict(result.symbol_map), rom, caplog.text + "\n".join(result.diagnostics))


def _write(root: Path, toml: str, files: dict[str, str | bytes]) -> None:
    """ff4's layout: sources under `src/`, headers included as `src/x.i`
    through the `.` include path."""
    (root / "a816.toml").write_text(toml.replace('"main.s"', '"src/main.s"'), encoding="utf-8")
    (root / "src").mkdir()
    for name, content in files.items():
        (root / "src" / name).write_bytes(content if isinstance(content, bytes) else content.encode())


def _build_and_check(caplog: pytest.LogCaptureFixture, seed: int, modules: list[str]) -> Built | None:
    """Cold build, warm with nothing changed, then warm after a comment lands in
    one module (it and its importers recompile): every build gives the cold ROM.
    None when the pool was legitimately full."""
    cold = _build(caplog)
    if not cold.ok:
        assert any(code in cold.log for code in EXHAUSTION), f"seed {seed}: build failed\n{cold.log}"
        return None
    assert _build(caplog).rom == cold.rom, f"seed {seed}: warm build differs from cold"
    edited = Path("src", f"{random.Random(seed).choice(modules)}.s")
    edited.write_text(edited.read_text(encoding="utf-8") + "; edited\n", encoding="utf-8")
    after_edit = _build(caplog)
    assert after_edit.ok, f"seed {seed}: warm build after editing {edited} failed\n{after_edit.log}"
    assert after_edit.rom == cold.rom, f"seed {seed}: warm build after editing {edited} differs from cold"
    return cold


# --- ROM pools ---------------------------------------------------------------


@dataclass
class RomPool:
    name: str
    ranges: list[tuple[int, int]]
    strategy: str
    reclaims: list[tuple[int, int]] = field(default_factory=list)

    def windows(self) -> list[tuple[int, int]]:
        return sorted(self.ranges + self.reclaims)


@dataclass
class Blob:
    name: str
    pool: str
    module: str
    data: bytes = b""
    align: int = 0
    cross_bank: bool = False
    pin: int | None = None
    calls: str | None = None  # code: nop / NAME_entry: / jsl CALLS / rtl

    @property
    def size(self) -> int:
        return 6 if self.calls else len(self.data)

    def directive(self) -> str:
        options = [
            f"at {self.pin:#08x}" if self.pin is not None else "",
            f"in {self.pool}",
            "cross_bank" if self.cross_bank else "",
            f"align {self.align:#x}" if self.align else "",
        ]
        body = (
            f"    nop\n{self.name}_entry:\n    jsl {self.calls}\n    rtl\n"
            if self.calls
            else f'    .incbin "{self.name}.bin"\n'
        )
        return f".alloc {self.name} {' '.join(filter(None, options))} {{\n{body}}}\n"


@dataclass
class RomProject:
    pools: list[RomPool]
    blobs: list[Blob]
    modules: list[str]


def _rom_pools(rnd: random.Random) -> tuple[list[RomPool], int]:
    bank = rnd.randrange(0x10, 0x60)
    pools = []
    for index in range(rnd.randint(1, 3)):
        ranges = []
        for _ in range(rnd.randint(1, 3)):
            ranges.append(((bank << 16) | 0x8000 + rnd.choice([0, 0x10, 0x100]), (bank << 16) | 0xFFFF))
            bank += 1
        pools.append(RomPool(f"pool{index}", ranges, rnd.choice(["pack", "order"])))
    return pools, bank


def _rom_blob(rnd: random.Random, index: int, pool: RomPool, module: str, callable_names: list[str]) -> Blob:
    blob = Blob(f"a{index}", pool.name, module)
    kind = rnd.random()
    if kind < 0.2 and callable_names:
        blob.calls = rnd.choice(callable_names)
        return blob
    blob.data = bytes(rnd.randrange(256) for _ in range(rnd.choice([1, 2, 7, 64, 255, 1000, 0x1234, 0x3000])))
    if kind < 0.35:
        blob.align = rnd.choice([2, 0x10, 0x100])
    elif kind < 0.45 and len(pool.ranges) > 1:
        blob.cross_bank = True
    return blob


def _rom_project(seed: int) -> RomProject:
    rnd = random.Random(seed)
    pools, bank = _rom_pools(rnd)
    modules = [f"m{i}" for i in range(rnd.randint(1, 5))]
    blobs: list[Blob] = []
    for index in range(rnd.randint(3, 14)):
        callable_names = [b.name for b in blobs if b.calls is None]
        blobs.append(_rom_blob(rnd, index, rnd.choice(pools), rnd.choice(modules), callable_names))
    pinned = rnd.choice(pools)
    if len(pinned.ranges) > 1 and rnd.random() < 0.6:
        blobs.append(Blob("pinned", pinned.name, rnd.choice(modules), bytes(range(16)), pin=pinned.ranges[1][0]))
    if rnd.random() < 0.5:
        rnd.choice(pools).reclaims.append(((bank << 16) | 0x8000, (bank << 16) | 0xBFFF))
    return RomProject(pools, blobs, modules)


def _rom_files(project: RomProject) -> dict[str, str | bytes]:
    header = "".join(
        f".pool {p.name} {{ {'  '.join(f'range {lo:#08x} {hi:#08x}' for lo, hi in p.ranges)}  strategy {p.strategy} }}\n"
        for p in project.pools
    ) + "".join(f".reclaim {p.name} {lo:#08x} {hi:#08x}\n" for p in project.pools for lo, hi in p.reclaims)
    files: dict[str, str | bytes] = {"pools.i": header, "main.s": "".join(f'.import "{m}"\n' for m in project.modules)}
    files.update({f"{b.name}.bin": b.data for b in project.blobs if b.calls is None})
    for module in project.modules:
        mine = [b for b in project.blobs if b.module == module]
        visible = {b.name for b in project.blobs if b.module <= module}
        externs = sorted({b.calls for b in mine if b.calls and b.calls not in visible})
        imports = "".join(f'.import "{other}"\n' for other in project.modules if other < module)
        files[f"{module}.s"] = (
            '.include "src/pools.i"\n'
            + "".join(f".extern {e}\n" for e in externs)
            + imports
            + "".join(b.directive() for b in mine)
        )
    return files


def _owns(pool: RomPool, start: int, end: int) -> bool:
    """`start..end` lies in one window, or runs on through contiguous ones."""
    spans = pool.windows()
    cursor = start
    for lo, hi in spans:
        if lo <= cursor <= hi:
            if end <= hi:
                return True
            following = [s for s, _ in spans if s > hi]
            if not following or _offset(hi) + 1 != _offset(following[0]):
                return False
            cursor = following[0]
    return False


def _rom_problems(project: RomProject, built: Built) -> list[str]:
    pools = {p.name: p for p in project.pools}
    problems: list[str] = []
    spans: list[tuple[int, int, str]] = []
    for blob in project.blobs:
        start = built.symbols[blob.name]
        end = start + blob.size - 1
        last = _offset(start) + blob.size - 1
        if blob.pin is not None and start != blob.pin:
            problems.append(f"{blob.name} pinned at {blob.pin:#x}, placed at {start:#x}")
        if not blob.cross_bank and (start >> 16) != (end >> 16):
            problems.append(f"{blob.name} {start:#x}..{end:#x} crosses a bank")
        if not _owns(pools[blob.pool], start, start if blob.cross_bank else end):
            problems.append(f"{blob.name} {start:#x} outside {blob.pool}")
        if blob.align and start % blob.align:
            problems.append(f"{blob.name} {start:#x} not {blob.align:#x}-aligned")
        problems.extend(_content_problems(blob, start, built))
        spans.append((_offset(start), last, blob.name))
    spans.sort()
    problems.extend(f"{a} overlaps {b}" for (_, end_a, a), (start_b, _, b) in pairwise(spans) if start_b <= end_a)
    return problems


def _content_problems(blob: Blob, start: int, built: Built) -> list[str]:
    got = built.rom[_offset(start) : _offset(start) + blob.size]
    if blob.calls is None:
        return [] if got == blob.data else [f"{blob.name}: ROM bytes at {start:#x} differ from the blob"]
    target = built.symbols[blob.calls]
    want = bytes([0xEA, 0x22, target & 0xFF, (target >> 8) & 0xFF, target >> 16, 0x6B])
    problems = [] if got == want else [f"{blob.name}: code {got.hex()} != {want.hex()}"]
    if built.symbols.get(f"{blob.name}_entry") != start + 1:
        problems.append(f"{blob.name}_entry is not {start + 1:#x}")
    return problems


@pytest.mark.parametrize("seed", SEEDS)
def test_rom_pools_hold_their_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    project = _rom_project(seed)
    _write(tmp_path, f'entrypoint = "main.s"\nrom_size = 0x400000\n{MAP}', _rom_files(project))
    monkeypatch.chdir(tmp_path)
    built = _build_and_check(caplog, seed, project.modules)

    assert built is None or _rom_problems(project, built) == [], f"seed {seed}"


# --- bss pools ---------------------------------------------------------------

WRAM = (0x7E2000, 0x7E2FFF)
ACTOR = ".struct Actor {\n    byte hp\n    word x\n    word y\n    byte[4] flags\n}\n"
ACTOR_FIELDS = {"hp": 0, "x": 1, "y": 3, "flags": 5}


@dataclass
class Reservation:
    name: str
    module: str
    context: str | None
    size: int
    typed: bool = False
    pin: int | None = None

    def directive(self) -> str:
        where = f"ram.{self.context}" if self.context else "ram"
        at = f" at {self.pin:#08x}" if self.pin is not None else ""
        kind = "as Actor" if self.typed else f"{self.size:#x}"
        return f".reserve {self.name} {kind}{at} in {where}\n"


def _reservations(seed: int) -> tuple[list[str], list[str], list[Reservation]]:
    rnd = random.Random(seed)
    contexts = [f"c{i}" for i in range(rnd.randint(1, 3))]
    modules = [f"m{i}" for i in range(rnd.randint(1, 4))]
    reservations = []
    for index in range(rnd.randint(2, 16)):
        typed = rnd.random() < 0.25
        size = 9 if typed else rnd.choice([1, 2, 0x10, 0x40, 0x123])
        reservations.append(Reservation(f"r{index}", rnd.choice(modules), rnd.choice([*contexts, None]), size, typed))
    if rnd.random() < 0.5:
        first = reservations[0]
        first.pin, first.context, first.typed, first.size = WRAM[0] + rnd.randrange(0, 0x800, 0x10), None, False, 0x20
    return contexts, modules, reservations


def _bss_files(contexts: list[str], modules: list[str], reservations: list[Reservation]) -> dict[str, str | bytes]:
    header = ACTOR + f".pool ram {{ bss  range {WRAM[0]:#08x} {WRAM[1]:#08x}  contexts {', '.join(contexts)} }}\n"
    # A reserve in the header every module includes: identical copies merge (ff4's rolling_state.i).
    header += ".reserve shared_scratch 2 in ram\n"
    loads = "".join(f"    lda.l {r.name}\n" for r in reservations)
    loads += "".join(f"    lda.l {r.name}.{f}\n" for r in reservations if r.typed for f in ACTOR_FIELDS)
    files: dict[str, str | bytes] = {
        "ram.i": header,
        "main.s": "".join(f'.import "{m}"\n' for m in modules) + f".alloc code at 0x008000 {{\n{loads}}}\n",
    }
    for module in modules:
        # Each module imports the ones before it: editing one recompiles its
        # importers without their own discovery parse (ff4's warm-build E0400).
        imports = "".join(f'.import "{other}"\n' for other in modules if other < module)
        files[f"{module}.s"] = (
            imports + '.include "src/ram.i"\n' + "".join(r.directive() for r in reservations if r.module == module)
        )
    return files


def _bss_problems(reservations: list[Reservation], built: Built) -> list[str]:
    problems: list[str] = []
    spans = []
    for r in reservations:
        start = built.symbols[r.name]
        if not (WRAM[0] <= start and start + r.size - 1 <= WRAM[1]):
            problems.append(f"{r.name} {start:#x} outside the pool")
        if r.pin is not None and start != r.pin:
            problems.append(f"{r.name} pinned at {r.pin:#x}, placed at {start:#x}")
        problems.extend(
            f"{r.name}.{f} misplaced"
            for f, delta in ACTOR_FIELDS.items()
            if r.typed and built.symbols.get(f"{r.name}.{f}") != start + delta
        )
        spans.append((start, start + r.size - 1, r))
    for index, (s1, e1, r1) in enumerate(spans):
        for s2, e2, r2 in spans[index + 1 :]:
            may_share = r1.context is not None and r2.context is not None and r1.context != r2.context
            if not may_share and s1 <= e2 and s2 <= e1:
                problems.append(f"{r1.name} ({r1.context}) overlaps {r2.name} ({r2.context})")
    targets = [built.symbols[r.name] for r in reservations]
    targets += [built.symbols[r.name] + d for r in reservations if r.typed for d in ACTOR_FIELDS.values()]
    operands = b"".join(bytes([0xAF, t & 0xFF, (t >> 8) & 0xFF, t >> 16]) for t in targets)
    if built.rom[: len(operands)] != operands:
        problems.append("lda.l operands differ from the symbols")
    return problems


@pytest.mark.parametrize("seed", SEEDS)
def test_bss_pools_hold_their_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    contexts, modules, reservations = _reservations(seed)
    toml = f'entrypoint = "main.s"\nrom_size = 0x100000\n{MAP}[map.2]\naddress = "7e-7f:0000-ffff"\nwritable = true\n'
    _write(tmp_path, toml, _bss_files(contexts, modules, reservations))
    monkeypatch.chdir(tmp_path)
    built = _build_and_check(caplog, seed, modules)

    assert built is None or _bss_problems(reservations, built) == [], f"seed {seed}"
