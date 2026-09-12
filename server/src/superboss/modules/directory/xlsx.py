"""Read the first worksheet of an .xlsx as header-keyed row dicts."""

from __future__ import annotations

import re
import zipfile
from datetime import date, timedelta
from io import BytesIO
from typing import Any
from xml.etree import ElementTree

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


def _col_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("m:si", _NS):
        texts = [
            node.text or ""
            for node in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        ]
        values.append("".join(texts).strip())
    return values


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        texts = [
            node.text or ""
            for node in cell.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        ]
        return "".join(texts).strip()
    value_node = cell.find("m:v", _NS)
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        index = int(raw)
        if 0 <= index < len(shared):
            return shared[index]
        return ""
    return raw.strip()


def _excel_date(value: str) -> date | None:
    if not value:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        serial = float(value)
        if serial < 20000:
            return None
        return date(1899, 12, 30) + timedelta(days=int(serial))
    return None


def read_sheet_rows(payload: bytes, *, header_row: int = 2) -> list[dict[str, Any]]:
    """Parse Sheet1. `header_row` is 1-based as shown in Excel."""
    archive = zipfile.ZipFile(BytesIO(payload))
    shared = _shared_strings(archive)
    sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    grid: dict[int, dict[int, str]] = {}
    for row in sheet.findall("m:sheetData/m:row", _NS):
        number = int(row.get("r") or 0)
        cells: dict[int, str] = {}
        for cell in row.findall("m:c", _NS):
            match = _CELL_REF.match(cell.get("r") or "")
            if match is None:
                continue
            cells[_col_index(match.group(1))] = _cell_text(cell, shared)
        if cells:
            grid[number] = cells
    headers = grid.get(header_row, {})
    if not headers:
        return []
    width = max(headers) + 1
    names = [
        re.sub(r"\s+", " ", (headers.get(index) or f"col_{index}")).strip()
        for index in range(width)
    ]
    records: list[dict[str, Any]] = []
    for number, cells in sorted(grid.items()):
        if number <= header_row:
            continue
        values = [cells.get(index, "") for index in range(width)]
        if not any(values):
            continue
        record: dict[str, Any] = {"_row": number}
        for name, value in zip(names, values, strict=False):
            if name:
                record[name] = value
        records.append(record)
    return records


def as_date(value: object) -> date | None:
    if value is None:
        return None
    return _excel_date(str(value).strip())
