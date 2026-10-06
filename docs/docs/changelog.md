# Changelog

## 1.1.0 "Swift-Tuttle"

Separate compilation and linking, declared memory layout (freespace and
`bss` pools), structs, a build cache, and fluff (lint, format, fix) plus
an LSP. Tested over the alphas against ff4, dq6, cacheguard, Bahamut
Lagoon and kintsuki.

### Highlights

Freespace pools: pinned blocks are carved first, the rest is placed
around them, and the layout is checked at link.

```ca65
.pool upper_gap { range 0x500000 0x5cffff  strategy pack }

.alloc dialogue_stream at 0x500000 in upper_gap cross_bank {
    .incbin "assets/stream.dat"
}
.alloc keep_font in upper_gap {
    .incbin "assets/keep_font_packed.dat"
}

.assert dialogue_stream + assets_stream_dat__size <= 0x5d0000, "the stream overruns the gap"
```

`bss` pools lay out WRAM, SRAM and VRAM without emitting bytes. Memory
used in turns goes in one pool's `contexts`; other overlaps are errors.

```ca65
.pool menu_ram { bss  range 0x7e9800 0x7e990f  contexts field_menu, treasure }

.reserve vwf_state as VwfState in menu_ram.field_menu
.reserve treasure_hdma 0x40 in menu_ram.treasure
```

Structs with bit fields, arrays and nesting; typed views; initialized
instances; `@std/snes/*` hardware register definitions.

```ca65
.import "@std/snes/ppu"

ppu := (PPU_BASE as PPU)
    lda ppu.INIDISP

player:
    .istruct Sprite {
        name = "HERO"
        pos = { x = 0x80, y = 0x60 }
        palette = 3
    }
```

### Upgrading from 1.0

- `a816 fix` rewrites `*= ADDR` placements as `.alloc at ADDR` (`UP001`).
  `*=` still assembles; the lint flags it.
- Every emitted byte needs a placement: an `.alloc` or a preceding `*=`.
  Bytes before the first `*=` used to land at `0x008000`, over whatever
  was there; they are now `E0310`.
- Assembling from Python in direct (single-pass) mode is deprecated;
  build through `build_with_imports` or the CLI.

### Placement

- `.pool NAME { range LO HI ... }` declares freespace, with `fill`,
  `strategy pack | order`, and ranges over several banks, split along
  the windows the bus serves.
- `.alloc NAME in POOL { ... }` lets the allocator choose the address;
  `.alloc [NAME] at ADDR [size N] { ... }` pins it, with `size` as a hard
  bound.
- `.alloc NAME at ADDR in POOL` pins a block inside a pool, which then
  places everything else around it.
- `align N` places a block on an `N`-byte boundary.
- `cross_bank` lets a data blob run over bank edges where the ROM is
  contiguous on the bus; code and labels are refused (`E0336`).
- `.relocate SYMBOL OLD_START OLD_END into POOL` moves a routine and
  gives its old space back; `.reclaim POOL START END` adds slack.
- `.assert EXPR, "message"` checks layout invariants once every address
  is final; every failed assert is reported (`E0407`).
- Pools merge across modules by name, and the linker places allocs from
  every object in one pass.
- Overlapping writes fail the build by default, naming both spans
  (`--overlap-mode warn | off`).
- An empty alloc body binds its label and takes no space.

### Memory pools

- `bss` pools reserve address space and emit nothing.
- `.reserve NAME SIZE [at ADDR] in POOL` and `.reserve NAME as TYPE`,
  which publishes `NAME.<field>` for each struct field.
- `contexts A, B` on a pool lets memory used in turns overlap; overlap
  between two different pools is `E0406`, with both reservations and
  their source lines.

### Language

- `.struct` with `byte`, `word`, `long`, `dword`, `uN` bit fields
  (with `.mask` and `.shift`), nested structs and `TYPE[N]` arrays;
  every field publishes its offset, and `Name.__size` gives the size.
- `(expr as T).field` casts and `view := (expr as T)` typed binds,
  over constants, labels and `.extern` symbols alike; the operand size
  follows the bind's base.
- `.istruct Type { ... }` emits an initialized instance: strings,
  lists, nested structs and bit fields, zero-filling the rest.
- `.label NAME = ADDR` names an address for debuggers and the LSP
  without emitting anything.
- `.a8` / `.a16` / `.i8` / `.i16`, and `rep` / `sep` tracking behind
  `--experimental track_register_size`, size immediates.
- The rest of the 65c816 instruction set: `brl`, `bvc`, `bvs`, `cld`,
  `cli`, `clv`, `cop`, `mvn`, `mvp`, `per` and `wdm`, plus `jsl` / `jml`
  as aliases of `jsr.l` / `jmp.l`. `brk`, `cop` and `wdm` take their
  signature byte explicitly.
- One expression grammar everywhere, with C precedence and semantics:
  truncating `/`, `%`, comparisons, `~` within 8, 16 or 32 bits;
  division by zero is `E0312`.
- Aliases inside a named scope publish as `scope.name`.

### Modules and linking

- Separate compilation: `a816 build -c` writes objects, and the linker
  combines them. `a816 build main.s` discovers and builds every import.
- `.import "@std/snes/ppu"` (and `cpu`, `dma`, `apu`, `joypad`, `wram`,
  `header`): typed SNES registers.
- `.extern` symbols work in any expression, macro or alias; the linker
  evaluates them once placed.
- Transitive imports are deduplicated, and modules are found on the
  module paths only, so a same-named file next door can't shadow one.
- Private `_labels` stay private per module and per alloc.
- The linker writes `.sym` and `.adbg` debug info with source mapping.

### Builds

- The build cache rebuilds a module when anything that shaped its object
  changed: defines, include and module paths, the bus map, file contents
  (a touch alone doesn't count), a lookup that would now find a new file,
  an import's output, or the toolchain's codegen revision. `--no-cache`
  compiles everything.
- Identical sources give an identical ROM, independent of
  `PYTHONHASHSEED`.
- `a816.toml` holds the project: `entrypoint`, `include-paths`,
  `module-paths`, a cartridge `board` from ares' `boards.bml` (LoROM,
  HiROM, ExHiROM, SA-1 and the rest), `[map.N]` regions in bsnes form,
  `rom_size` and `[experimental]` flags.

### Diagnostics

- Errors carry a stable code (`E0001` to `E0509`), a caret on the
  offending token, a hint and, for names, a did-you-mean;
  `a816 explain CODE` documents each one. A few older codegen errors
  still have no code.
- An unsized operand naming a symbol resolved at link (`jmp target`
  with `target` from another module) asks for its size (`E0313`), since
  the right form depends on where the linker puts it.
- An undefined symbol passed to a macro is reported where the caller
  wrote it, with the macro parameter it was bound to.
- Several errors from one pass are reported together.
- Pool overflows say whether the block is too big, the pool fragmented
  or full, and what to try.
- A duplicate global names both definitions and their values (`E0400`).
- Logs stay quiet by default; `--verbose` shows tracebacks.

### Fluff: lint, format, fix

- `a816 format [--check]`: a canonical formatter that keeps comments,
  docstrings and strings intact and settles in one pass.
- `a816 check`: docstring rules (`DOC001` to `DOC007`), naming (`N801`,
  `N802`), struct casts (`S001`, `S003`, `S004`), line length (`E501`),
  redundant size suffixes (`OP001`) and legacy placement (`UP001`);
  `; noqa: RULE` to suppress.
- `a816 fix` applies the autofixes, also offered as LSP code actions.

### Language server

- Hover with docstrings (modules included), struct fields, bit fields
  and pools; go-to-definition into includes, imports and `.incbin`
  assets; completion, rename, workspace symbols and semantic tokens.
- Diagnostics with error codes and hints, recovery from broken input,
  and an index that builds in the background.

### Tools

- `xobj` inspects object files: sections, symbols, relocations, pools
  and the format identity.
- `xdds` disassembles with the same instruction table the assembler uses.
- `A816_EMIT_TRACE=1` logs where every region landed.
