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
    text = format_attachment_excerpt("scan.png", state=FileState.CLEAN, data=b"\x89PNG")
    assert "scan.png" in text
    assert "不支持的文件类型" in text or "无法抽取" in text
