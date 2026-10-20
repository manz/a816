# GitHub actions

a816 ships the CI steps a ROM hack repository repeats: install, decrypt
the base ROM, lint, build, seal, a pull request's artifact comment, the
nightly prerelease and the tagged release. Each one is a composite action
under `manz/a816/actions/` that calls `a816 ci <step>`, so it runs the a816
the project locks. The actions need a816 1.1.5 or later, and pin the same
tag as the package.

| Action | Does |
|---|---|
| `setup` | `uv sync --locked`, then exports `A816_BUILD_VERSION` (the tag, `pr-<n>` or `nightly-<run>`) and `SOURCE_DATE_EPOCH` (the commit time, unless the job sets it). |
| `decrypt-rom` | Decrypts `x.sfc.gz.gpg` with `passphrase`; `available` is `false`, and nothing fails, without one (a fork's pull request). Optional `sha1`. |
| `lint` | `a816 format --diff`, then `a816 check`; both run. No `paths`: the project `a816.toml` describes. |
| `build` | `a816 build` to several `outputs` (one per line, format from the extension), with `defines`. Output `debug-info`. |
| `seal` | `a816 bps seal` for each `x.bps.unsealed`, `a816 ips seal` for each `x.ips` in `patches`, against `rom`: the SNES header checksum is written when `a816.toml` declares the cartridge (`checksum: false` to skip). |
| `pr-artifact` | Uploads `files` as `<name>-pr-<n>` and keeps one comment, found by `marker`, linking it; deletes it when `outcome` is not `success`. |
| `nightly` | Replaces the one `nightly` prerelease and its tag with `files`. |
| `release` | Creates the tag's release with `assets` and an optional reproducible `zip`; prerelease for PEP 440 pre-release tags (`v1.2rc1`). A re-run re-uploads and keeps the notes. |

Steps with their own build script (`build.py`, `make`) keep it as a plain
step: the actions around it do not care how the patch was built.

## A project

```yaml
# .github/workflows/ci.yml
name: CI
on:
  pull_request:
  push:
    branches: [master]

concurrency:
  group: ci-${{ github.ref }}

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: write        # the nightly release
      issues: write          # the PR comment
      pull-requests: write
    steps:
      - uses: actions/checkout@v7
      - uses: manz/a816/actions/setup@v1.1.5
      - id: rom
        uses: manz/a816/actions/decrypt-rom@v1.1.5
        with:
          passphrase: ${{ secrets.ROM_PASSPHRASE }}
          encrypted: game.sfc.gz.gpg
          to: build/game.sfc
      - if: steps.rom.outputs.available == 'true'
        uses: manz/a816/actions/build@v1.1.5
        with:
          outputs: |
            build/game.ips
            build/game.bps.unsealed
      - uses: manz/a816/actions/lint@v1.1.5
      - if: steps.rom.outputs.available == 'true'
        uses: manz/a816/actions/seal@v1.1.5
        with:
          rom: build/game.sfc
          patches: build/game.bps.unsealed
      - if: always() && github.event_name == 'pull_request' && steps.rom.outputs.available == 'true'
        uses: manz/a816/actions/pr-artifact@v1.1.5
        with:
          name: game
          files: |
            build/game.ips
            build/game.bps
          marker: GAME-BUILD-COMMENT
          outcome: ${{ job.status }}
      - if: github.event_name == 'push'
        uses: manz/a816/actions/nightly@v1.1.5
        with:
          files: |
            build/game.ips
            build/game.bps
```

```yaml
# .github/workflows/release.yml
name: Release
on:
  push:
    tags: ['v*']

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v7
      - uses: manz/a816/actions/setup@v1.1.5
      - uses: manz/a816/actions/decrypt-rom@v1.1.5
        with:
          passphrase: ${{ secrets.ROM_PASSPHRASE }}
          encrypted: game.sfc.gz.gpg
          to: build/game.sfc
      - uses: manz/a816/actions/build@v1.1.5
        with:
          outputs: |
            build/game.ips
            build/game.bps.unsealed
      - uses: manz/a816/actions/seal@v1.1.5
        with:
          rom: build/game.sfc
          patches: build/game.bps.unsealed
      - uses: manz/a816/actions/release@v1.1.5
        with:
          zip: game-${{ github.ref_name }}.zip
          zip-files: |
            build/game.ips
            build/game.bps
            README.txt
```

The build sees `A816_BUILD_VERSION`; pass it on with `defines: VERSION=...`
or read it in a build script. Lint after any step that generates sources
the project includes.

The base ROM is never committed in clear: commit `gzip -9 < game.sfc | gpg
--symmetric > game.sfc.gz.gpg` and store the passphrase as a secret.

## Trying a step locally

Every step is `a816 ci <step>`, so it runs outside GitHub too: outputs
print as `name=value`. The publishing steps take `--dry-run`, which prints
the `gh` writes instead of running them:

```sh
GITHUB_REF_NAME=v1.2rc1 a816 ci release --dry-run --assets='build/*.ips'
```

## Renovate

The actions and the a816 package move together. Extend the preset so
Renovate bumps both in one pull request:

```json
{
  "extends": ["github>manz/a816//renovate/a816"]
}
```
