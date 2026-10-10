"""W0001 at link: a reference that binds to a path-derived `.incbin` name.

The compile-time check (`code_gen`) sees a unit and its imports. A module
that names `assets_vwf_bin` through `.extern`, never importing the module
whose `.incbin` binds it, only meets the name at link; ff4 had three such
references. Each object lists the path names it binds and the ones its
compile already warned on, so the linker warns on the rest, once per site.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from a816.error_codes import W_INCBIN_PATH_NAME
from a816.errors import SourceLocation, format_error
from a816.object_file import PC_RELATIVE_PREFIX, ObjectFile, Section

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


@dataclass(frozen=True)
class RelocationSite:
    """A linked relocation: the object that emitted it, its section, the
    operand's offset there, and the operand (a symbol or an expression)."""

    obj_idx: int
    section: Section
    offset: int
    operand: str


def path_name_warnings(
    objects: Sequence[ObjectFile],
    sites: Iterable[RelocationSite],
    files: Sequence[str],
    locals_by_obj: dict[int, dict[str, int]],
) -> list[str]:
    """One formatted W0001 per site whose operand names a path-derived name
    its object's compile did not check (the object's own LOCALs aside)."""
    records = {record.name: record for obj in objects for record in obj.path_names}
    if not records:
        return []
    checked = [set(obj.checked_path_names) for obj in objects]
    warnings: dict[tuple[int, str, str, int], str] = {}
    for site in sites:
        local = locals_by_obj.get(site.obj_idx, {})
        for name in _IDENTIFIER.findall(site.operand.removeprefix(PC_RELATIVE_PREFIX)):
            record = records.get(name)
            if record is None or name in checked[site.obj_idx] or name in local:
                continue
            location = _location(site.section, site.offset, files, name)
            where = (location.filename, location.line) if location else ("", site.offset)
            key = (site.obj_idx, name, *where)
            message = f"`{name}` is named after the asset path {record.file_path!r}"
            warnings.setdefault(
                key, format_error(message, location, "warning", hint=record.hint, code=str(W_INCBIN_PATH_NAME))
            )
    return list(warnings.values())


def _location(section: Section, offset: int, files: Sequence[str], name: str) -> SourceLocation | None:
    """The source line the operand at `offset` came from, the caret on `name`."""
    entries = [entry for entry in section.lines if entry[0] <= offset]
    if not entries:
        return None
    _offset, file_idx, line, column, _flags = max(entries, key=lambda entry: entry[0])
    if not 0 <= file_idx < len(files):
        return None
    source_line = _source_line(files[file_idx], line)
    found = re.search(rf"(?<![\w.]){re.escape(name)}(?![\w.])", source_line)
    if found is not None:
        column = found.start()
    return SourceLocation(files[file_idx], line, column, source_line, length=len(name))


def _source_line(path: str, line: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ""
    return lines[line] if 0 <= line < len(lines) else ""
