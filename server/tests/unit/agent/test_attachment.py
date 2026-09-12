"""Attachment excerpts skip dirty files and inline clean text."""

from superboss.modules.agent.service import format_attachment_excerpt
from superboss.modules.files.models import FileState


def test_clean_txt_excerpt_includes_text() -> None:
    text = format_attachment_excerpt(
        "brief.txt", state=FileState.CLEAN, data="星野合作默认三个里程碑".encode()
    )
    assert "brief.txt" in text
    assert "星野合作" in text


def test_unclean_file_skips_bytes() -> None:
    text = format_attachment_excerpt(
        "secret.txt", state=FileState.SCANNING, data="不该出现的正文".encode()
    )
    assert "尚未通过扫描" in text
    assert "不该出现的正文" not in text


def test_missing_bytes_uses_filename_placeholder() -> None:
    text = format_attachment_excerpt("brief.txt", state=FileState.CLEAN, data=None)
    assert text == "[附件 brief.txt]"


def test_extract_error_is_inlined() -> None:
    text = format_attachment_excerpt("scan.bin", state=FileState.CLEAN, data=b"\x00\x01")
    assert "scan.bin" in text
    assert "不支持的文件类型" in text or "无法抽取" in text


def test_xlsx_excerpt_is_supported() -> None:
    from io import BytesIO
    from zipfile import ZipFile

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1"><c r="A1"><v>类型</v></c><c r="B1"><v>金额</v></c></row>
<row r="2"><c r="A2"><v>成本</v></c><c r="B2"><v>100</v></c></row>
</sheetData>
</worksheet>""",
        )
    text = format_attachment_excerpt("会计账本.xlsx", state=FileState.CLEAN, data=buffer.getvalue())
    assert "不支持的文件类型" not in text
    assert "会计账本.xlsx" in text


def test_truncated_xlsx_does_not_raise() -> None:
    text = format_attachment_excerpt(
        "会计账本.xlsx", state=FileState.CLEAN, data=b"PK\x03\x04truncated"
    )
    assert "会计账本.xlsx" in text
    assert "无法" in text or "完整" in text
