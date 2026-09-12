"""Directory import keeps source rows unmerged and masks phones for staff."""

import zipfile
from io import BytesIO

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.config import Settings
from superboss.main import create_app
from superboss.modules.directory.service import map_row, mask_phone
from superboss.modules.users.models import User
from tests.identity import LOCAL_TEST_PASSWORD, local_user

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    shared = ["title", *headers, *[cell for row in rows for cell in row]]
    strings = "".join(f'<si><t xml:space="preserve">{value}</t></si>' for value in shared)

    def cell(col: int, row: int, index: int) -> str:
        letter = chr(ord("A") + col)
        return f'<c r="{letter}{row}" t="s"><v>{index}</v></c>'

    header_xml = "".join(cell(index, 2, index + 1) for index in range(len(headers)))
    body = []
    cursor = 1 + len(headers)
    for offset, row in enumerate(rows):
        cells = "".join(cell(index, 3 + offset, cursor + index) for index in range(len(row)))
        body.append(f'<row r="{3 + offset}">{cells}</row>')
        cursor += len(row)
    sheet = (
        f'<worksheet xmlns="{_NS}"><sheetData>'
        f'<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        f'<row r="2">{header_xml}</row>'
        f"{''.join(body)}</sheetData></worksheet>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="{_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="{_NS}" count="{len(shared)}" uniqueCount="{len(shared)}">{strings}</sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml", f'<?xml version="1.0" encoding="UTF-8"?>{sheet}'
        )
    return buffer.getvalue()


@pytest_asyncio.fixture
async def directory_client(db_session: AsyncSession, test_settings: Settings, active_owner: User):
    del active_owner
    await db_session.commit()
    app = create_app(test_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _login(client: TestClient, username: str = "owner") -> None:
    assert client.get("/api/v1/auth/csrf").status_code == 204
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": LOCAL_TEST_PASSWORD},
            headers={"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))},
        ).status_code
        == 204
    )


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": str(client.cookies.get("XSRF-TOKEN"))}


def test_inline_str_cells_are_read() -> None:
    from superboss.modules.directory.xlsx import read_sheet_rows

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = f'''<worksheet xmlns="{ns}"><sheetData>
    <row r="2"><c r="A2" t="inlineStr"><is><t>物业小区名称</t></is></c></row>
    <row r="3"><c r="A3" t="inlineStr"><is><t>inline小区</t></is></c></row>
    </sheetData></worksheet>'''
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            f"""<?xml version="1.0"?><workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        )
        archive.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0"?>{sheet}')
    rows = read_sheet_rows(buffer.getvalue(), header_row=2)
    assert rows[0]["物业小区名称"] == "inline小区"


def test_mask_phone_keeps_ends() -> None:
    assert mask_phone("18659696336") == "186****6336"


def test_map_row_does_not_invent_district_for_xiangcheng() -> None:
    mapped = map_row(
        {
            "_row": 3,
            "物业小区名称": "万科城玖龙台",
            "物业企业全称": "万科物业",
            "小区所在街道": "芝山街道",
            "小区所在社区": "金品社区",
            "总户数（户）": "918",
        },
        "2026.7.29芗城区有备案物业小区（202个）.xlsx",
        "芗城区",
    )
    assert mapped["district"] == "芗城区"
    assert mapped["name"] == "万科城玖龙台"
    assert mapped["source_row"] == 3


@pytest.mark.asyncio
async def test_owner_imports_two_same_name_rows_separately(
    directory_client: TestClient,
) -> None:
    client = directory_client
    _login(client)
    headers = [
        "物业小区名称",
        "县区",
        "小区所在街道 （乡镇）",
        "小区所在社区（村）",
        "物业企业全称",
        "总户数（户）",
        "联系手机",
    ]
    payload = _xlsx(
        headers,
        [
            ["同名花园", "芗城区", "东铺头街道", "A社区", "甲物业", "100", "13800001111"],
            ["同名花园", "芗城区", "西桥街道", "B社区", "乙物业", "200", "13900002222"],
        ],
    )
    imported = client.post(
        "/api/v1/directory/imports",
        files={
            "file": (
                "芗城同名.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_csrf(client),
    )
    assert imported.status_code == 200
    assert imported.json()["inserted"] == 2
    listed = client.get("/api/v1/directory")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    names = [item["name"] for item in body["items"]]
    assert names == ["同名花园", "同名花园"]
    streets = {item["street"] for item in body["items"]}
    assert streets == {"东铺头街道", "西桥街道"}
    assert all(item["phone"] for item in body["items"])
    assert all("****" in item["phone_masked"] for item in body["items"])


@pytest.mark.asyncio
async def test_staff_sees_masked_phone_and_cannot_import(
    directory_client: TestClient, db_session: AsyncSession
) -> None:
    client = directory_client
    db_session.add(local_user("staff-1", display_name="Staff"))
    await db_session.commit()
    _login(client)
    headers = [
        "物业小区名称",
        "县区",
        "小区所在街道 （乡镇）",
        "小区所在社区（村）",
        "物业企业全称",
        "总户数（户）",
        "联系手机",
    ]
    payload = _xlsx(
        headers, [["港城御龙湾", "龙文区", "步文街道", "天亭社区", "天利", "969", "18659696336"]]
    )
    assert (
        client.post(
            "/api/v1/directory/imports",
            files={"file": ("龙文.xlsx", payload)},
            headers=_csrf(client),
        ).status_code
        == 200
    )
    client.cookies.clear()
    _login(client, "staff-1")
    listed = client.get("/api/v1/directory", params={"q": "港城御龙湾"})
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["name"] == "港城御龙湾"
    assert item["phone"] is None
    assert item["phone_masked"] == "186****6336"
    assert item["extra"] == {}
    denied = client.post(
        "/api/v1/directory/imports",
        files={"file": ("龙文.xlsx", payload)},
        headers=_csrf(client),
    )
    assert denied.status_code == 403


def test_convert_directory_entry_to_project(directory_client: TestClient) -> None:
    client = directory_client
    _login(client)
    headers = [
        "物业小区名称",
        "县区",
        "小区所在街道 （乡镇）",
        "小区所在社区（村）",
        "物业企业全称",
        "总户数（户）",
        "联系手机",
    ]
    payload = _xlsx(
        headers,
        [["云栖里", "龙文区", "步文街道", "天亭社区", "天利", "100", "13800138000"]],
    )
    imported = client.post(
        "/api/v1/directory/imports",
        files={"file": ("龙文.xlsx", payload)},
        headers=_csrf(client),
    )
    assert imported.status_code == 200
    listed = client.get("/api/v1/directory", params={"q": "云栖里"})
    entry_id = listed.json()["items"][0]["id"]
    converted = client.post(
        f"/api/v1/directory/{entry_id}/convert-project",
        json={"starts_on": "2026-09-01", "service_fee_cents": 3_000_000},
        headers=_csrf(client),
    )
    assert converted.status_code == 201
    assert converted.json()["name"] == "云栖里会务"
    assert len(converted.json()["nodes"]) == 7
    again = client.get(f"/api/v1/directory/{entry_id}")
    assert again.json()["project_id"] == converted.json()["id"]
