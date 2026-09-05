"""Knowledge text extraction without OCR."""

from io import BytesIO

import pytest

from superboss.modules.knowledge.extract import ExtractError, extract_text


def test_extracts_markdown() -> None:
    text = extract_text("note.md", "# 星野\n合作节奏".encode())
    assert "星野" in text


def test_extracts_docx_paragraphs() -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("星野合作默认三个里程碑")
    buffer = BytesIO()
    document.save(buffer)
    text = extract_text("brief.docx", buffer.getvalue())
    assert "星野合作" in text


def test_blank_pdf_asks_for_text_version() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(ExtractError, match="文本版"):
        extract_text("empty.pdf", buffer.getvalue())


def test_rejects_images_without_ocr() -> None:
    with pytest.raises(ExtractError):
        extract_text("scan.png", b"\x89PNG")
