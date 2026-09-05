"""Extract text from uploaded knowledge files. No OCR."""

from pathlib import Path


class ExtractError(Exception):
    def __init__(self, message: str = "该文件无法抽取文字，请提供文本版") -> None:
        super().__init__(message)


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return data.decode("utf-8", errors="replace").strip()
    if suffix == ".docx":
        return _docx(data)
    if suffix == ".pdf":
        return _pdf(data)
    raise ExtractError("不支持的文件类型，请提供 txt、md、docx 或带文字层的 pdf")


def _docx(data: bytes) -> str:
    try:
        from io import BytesIO

        import docx  # type: ignore[import-untyped]
    except ImportError as error:
        raise ExtractError("当前环境未安装 python-docx") from error
    document = docx.Document(BytesIO(data))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
    if not text:
        raise ExtractError()
    return text


def _pdf(data: bytes) -> str:
    try:
        from io import BytesIO

        from pypdf import PdfReader
    except ImportError as error:
        raise ExtractError("当前环境未安装 pypdf") from error
    reader = PdfReader(BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if not text:
        raise ExtractError()
    return text
