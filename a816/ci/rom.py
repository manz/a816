"""`a816 ci decrypt-rom`: the gpg-encrypted base ROM a repository carries."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

from a816.ci.actions import Actions
from a816.ci.process import CiError, Run

PASSPHRASE_VAR = "A816_ROM_PASSPHRASE"


def decrypt_rom(encrypted: Path, to: Path, *, passphrase: str, sha1: str, run: Run) -> bool:
    """Decrypt `encrypted` (gunzipped when `.gz.gpg`) to `to`.

    Returns False, writing nothing, without a passphrase: a fork's pull
    request gets no secrets. The passphrase goes to gpg on stdin, never argv.
    """
    if not passphrase:
        return False
    data = run(
        [
            "gpg",
            "--quiet",
            "--batch",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase-fd",
            "0",
            "--decrypt",
            str(encrypted),
        ],
        passphrase.encode(),
    )
    if encrypted.name.endswith(".gz.gpg"):
        data = gzip.decompress(data)
    if sha1:
        # SHA-1 identifies the dump (No-Intro publishes it); it guards against a wrong ROM, not an attacker.
        actual = hashlib.sha1(data, usedforsecurity=False).hexdigest()  # NOSONAR
        if actual != sha1.lower():
            raise CiError(f"{encrypted} decrypts to a ROM with SHA-1 {actual}, expected {sha1.lower()}")
    to.parent.mkdir(parents=True, exist_ok=True)
    to.write_bytes(data)
    return True


def run_decrypt_rom(actions: Actions, run: Run, *, encrypted: Path, to: Path, sha1: str) -> int:
    passphrase = actions.get(PASSPHRASE_VAR)
    available = decrypt_rom(encrypted, to, passphrase=passphrase, sha1=sha1, run=run)
    if not available:
        print(f"no {PASSPHRASE_VAR} (a fork's pull request?): the ROM steps skip")
    actions.output("available", "true" if available else "false")
    return 0
