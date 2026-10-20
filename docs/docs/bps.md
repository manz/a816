# BPS patches

BPS (byuu's beat format) is the patch format emulators and patchers
prefer to IPS. Besides the changed bytes, a BPS patch records the size
and CRC32 of the base ROM, of the patched ROM and of the patch itself,
so a wrong or headered base ROM fails with an error instead of a broken
game.

## Build, then seal

The assembler never reads the base ROM. That leaves one value it
cannot compute: the patched ROM's CRC32, which covers every byte the
patch does not write. So a BPS build takes two steps:

```
$ a816 build ff4.s -f bps -o build/ff4.bps.unsealed
$ a816 bps seal build/ff4.bps.unsealed --source rom/ff4.sfc
sealed build/ff4.bps (patched ROM CRC32 1A2B3C4D)
```

1. `a816 build -f bps` writes the patch from the linked blocks, with
   the patched ROM's CRC32 left zero. The base ROM's size and CRC32,
   and the metadata, come from `[bps]` in `a816.toml` (see
   [Project configuration](index.md#bps-patches-bps)).
2. `a816 bps seal`, run where the base ROM is, checks it against the
   size and CRC32 the patch expects, applies the patch in memory and
   writes the patched ROM's CRC32 (and the patch's own) into the
   trailer. When `a816.toml` declares the cartridge (`board` or
   `[map.N]`, and `rom_size`), it first writes the SNES header
   checksum, which also depends on the base ROM (see below).

The IPS and the BPS can come from one link:
`a816 build ff4.s -o build/ff4.ips -o build/ff4.bps.unsealed` (from
Python, `build_with_imports(..., extra_outputs=[("bps", path)])`).

An unsealed patch is not valid BPS: every patcher rejects it on the
CRC. Naming it `*.bps.unsealed` keeps it from being shipped by mistake;
`seal` writes `PATCH` without `.unsealed` unless `-o` names the output,
and refuses a patch with another name and no `-o`.

## Commands

```
a816 bps seal PATCH --source ROM [-o OUT] [--no-checksum]
a816 ips seal PATCH --source ROM [-o OUT]
a816 bps apply PATCH --source ROM -o OUT [--unsealed]
a816 bps info PATCH
```

- `apply` checks all three CRCs; `--unsealed` skips the patched ROM's,
  to try a patch before sealing it. It applies any BPS patch, Flips'
  delta ones included.
- `info` prints the sizes, the CRCs (`unsealed` for a zero target CRC)
  and the metadata.
- A wrong base ROM is `E0512`, and one exactly 512 bytes too long is
  named as carrying a copier header. A patch that is not BPS, is
  truncated or fails a CRC is `E0511`.

## The header checksum

The SNES header holds the 16-bit sum of every ROM byte (at `$00:FFDE`,
its complement at `$00:FFDC`). A patch built without the base ROM
cannot know it, so emulators and flash carts that check it see a bad
checksum. Sealing has the base ROM, so it writes it:

- the header is found through the project's bus map: bus `$00:FFC0` is
  ROM offset `0x7FC0` on LoROM, `0xFFC0` on HiROM;
- the sum covers the ROM padded to `rom_size`; a size that is not a
  power of two counts its last part mirrored, as the bus does;
- `a816 bps seal` writes it into the patched ROM and encodes the patch
  again from its differences with the base ROM, metadata kept.
  `--no-checksum` leaves the header as the build wrote it;
- `a816 ips seal` appends the record that writes it, after a pad
  record when the patch does not reach `rom_size`. It writes the patch
  in place unless `-o` names another file, and sealing again replaces
  that record. It needs the cartridge (`E0519`) and a headerless base
  ROM (`E0518`).

Both run from the project directory, where `a816.toml` is found.

## The patch a816 writes

- Linear: bytes no block writes are read from the base ROM, each block
  is written out literally. Past the end of the base ROM, unwritten
  bytes are zeros, encoded as a run, so expanding a 1 MB ROM to 2 MB
  costs a few bytes.
- Blocks overlapping each other resolve the way applying the IPS
  does: the later one wins (only under `--overlap-mode warn`; an
  overlap is an error by default).
- The patched ROM is `rom_size` long when the project sets it, else as
  long as the base ROM or the last block.
- No `--copier-header` (`E0515`): BPS records a headerless ROM.
