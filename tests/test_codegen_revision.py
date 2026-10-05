"""`CODEGEN_REVISION` moves exactly when a816's output does.

The build cache keys objects on (format schema, codegen revision): a release
that changes what a816 emits for unchanged source must bump the revision, or
cached objects from the old a816 keep the old bytes. This test assembles a
fixed corpus (the integration ROM and `golden/corpus/*.s`) and hashes the
objects, with the paths that depend on where the tree lives removed. The
checked-in golden pairs that hash with the revision it was produced under and
the corpus sources it was produced from:

- corpus edited: refresh the golden (same revision);
- output changed, corpus and revision didn't: bump `CODEGEN_REVISION`, then refresh;
- revision bumped, output didn't change: the bump is spurious, revert it;
- revision bumped and output changed: refresh the golden.

Refresh with `UPDATE_CODEGEN_GOLDEN=1 hatch run tests:tests tests/test_codegen_revision.py`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from a816.module_builder import build_with_imports
from a816.object_file import CODEGEN_REVISION, ObjectFile

_TESTS = Path(__file__).parent
_INTEGRATION = _TESTS / "integration" / "basic" / "main.s"
_PLACEMENT = sorted((_TESTS / "golden" / "corpus").glob("*.s"))
_GOLDEN = _TESTS / "golden" / "codegen_revision.json"


def _canonical(value: Any) -> Any:
    """`value` as plain JSON data, leaving out dataclass fields still at their
    default: a field added to the format (with a default) changes the schema,
    not the output, so it must not move this digest."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {}
        for f in dataclasses.fields(value):
            field_value = getattr(value, f.name)
            default = f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default
            if default is dataclasses.MISSING or field_value != default:
                out[f.name] = _canonical(field_value)
        return out
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    return value


def _output_digest(obj_dirs: list[Path]) -> str:
    """Hash of every object's codegen output, independent of the tree's
    location and of fields the format gained since."""
    digest = hashlib.sha256()
    for obj_dir in obj_dirs:
        for path in sorted(obj_dir.glob("*.o")):
            wire = ObjectFile.from_file(str(path)).wire()
            wire = dataclasses.replace(
                wire, files=[], pool_allocs=[dataclasses.replace(a, source="") for a in wire.pool_allocs]
            )
            digest.update(path.name.encode())
            digest.update(json.dumps(_canonical(wire), sort_keys=True).encode())
    return digest.hexdigest()


def _corpus_digest() -> str:
    """Hash of the corpus sources: editing them changes the output without codegen changing."""
    digest = hashlib.sha256()
    sources = [*sorted(_INTEGRATION.parent.glob("*.s")), *_PLACEMENT]
    for path in sources:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _build(main: Path, obj_dir: Path, include_paths: list[Path]) -> None:
    prev = Path.cwd()
    os.chdir(main.parent)  # assets resolve from the source's directory
    try:
        result = build_with_imports(
            main_source=main,
            output_file=obj_dir.parent / f"{main.stem}.sfc",
            output_format="sfc",
            module_paths=[main.parent],
            include_paths=include_paths,
            output_dir=obj_dir,
            use_cache=False,
        )
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.diagnostics


def _build_corpus(tmp_path: Path) -> str:
    obj_dirs = [tmp_path / "integration"]
    _build(_INTEGRATION, obj_dirs[0], [_INTEGRATION.parent.parent, _INTEGRATION.parent])
    for source in _PLACEMENT:
        obj_dirs.append(tmp_path / source.stem)
        _build(source, obj_dirs[-1], [source.parent])
    return _output_digest(obj_dirs)


def test_codegen_revision_tracks_the_output(tmp_path: Path) -> None:
    digest, corpus = _build_corpus(tmp_path), _corpus_digest()
    if os.environ.get("UPDATE_CODEGEN_GOLDEN"):
        _GOLDEN.write_text(
            json.dumps({"revision": CODEGEN_REVISION, "corpus": corpus, "digest": digest}, indent=1) + "\n"
        )
        pytest.skip("golden refreshed")
    golden = json.loads(_GOLDEN.read_text())
    if corpus != golden["corpus"]:
        pytest.fail("the corpus changed: refresh the golden (UPDATE_CODEGEN_GOLDEN=1), keeping CODEGEN_REVISION")
    if digest == golden["digest"]:
        assert CODEGEN_REVISION == golden["revision"], (
            f"CODEGEN_REVISION moved to {CODEGEN_REVISION} but the output didn't change: revert the bump"
        )
        return
    assert CODEGEN_REVISION != golden["revision"], (
        "codegen output changed: bump CODEGEN_REVISION in a816/object_file.py, then refresh the golden "
        "(UPDATE_CODEGEN_GOLDEN=1)"
    )
    pytest.fail("CODEGEN_REVISION bumped: refresh the golden (UPDATE_CODEGEN_GOLDEN=1) to pair it with the new output")
