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
    if suffix in {".xlsx", ".xlsm"}:
        return _xlsx(data)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return (
            f"图片附件 {filename} 已接收，当前不进行 OCR。"
            "请使用 import_finance_from_file 结合账本登记，或说明该凭证对应哪一笔费用。"
        )
    if suffix == ".zip":
        return _zip(data)
    raise ExtractError("不支持的文件类型，请提供 txt、md、docx、xlsx、图片、压缩包或带文字层的 pdf")


def _docx(data: bytes) -> str:
    try:
        from io import BytesIO

        import docx
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


def _xlsx(data: bytes) -> str:
    import zipfile

    from superboss.modules.directory.xlsx import read_sheet_rows

    try:
        rows = read_sheet_rows(data, header_row=1)
        if not rows:
            rows = read_sheet_rows(data, header_row=2)
    except zipfile.BadZipFile as error:
        raise ExtractError("Excel 无法作为完整工作簿解析") from error
    if not rows:
        raise ExtractError("Excel 中没有可读行")
    lines: list[str] = []
    for record in rows[:40]:
        values = [f"{key}={record[key]}" for key in record if key != "_row" and record.get(key)]
        lines.append(f"第{record.get('_row')}行 " + " | ".join(values))
    return "\n".join(lines)


def _zip(data: bytes) -> str:
    import zipfile
    from io import BytesIO

    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ExtractError("压缩包无法打开") from error
    names = [item for item in archive.namelist() if not item.endswith("/")]
    if not names:
        raise ExtractError("压缩包是空的")
    preview = "、".join(names[:30])
    return f"压缩包内文件：{preview}"
