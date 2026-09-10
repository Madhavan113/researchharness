"""Binary fixture bytes remain identical in domain and subprocess transports."""

import base64
import io
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from research_harness.evaluation.fixture_transport import FixtureSources
from research_harness.evaluation.fixtures import fixture_response_bytes


def test_binary_source_transport_preserves_exact_bytes(tmp_path):
    payload = b"PK\x00\xff\x80\x0d\x0a"
    response = {
        "url": "https://example.test/rates.xlsx",
        "body_base64": base64.b64encode(payload).decode(),
    }
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"search_results": [], "responses": [response]}))
    assert fixture_response_bytes(response) == payload
    with FixtureSources(path).client() as client:
        assert client.get(response["url"]).content == payload


@pytest.mark.parametrize(
    "response", [{}, {"body": "", "body_base64": ""}, {"body_base64": "%%%"}, {"body_base64": 3}]
)
def test_missing_ambiguous_or_malformed_body_encoding_is_rejected(response):
    with pytest.raises(ValueError):
        fixture_response_bytes(response)


def test_workbook_fixture_contains_actual_monthly_cells():
    path = (
        Path(__file__).resolve().parents[1]
        / "examples/evaluation/development/fixtures/rates-workbook-unsupported.json"
    )
    response = next(
        item for item in json.loads(path.read_bytes())["responses"] if item["url"].endswith(".xlsx")
    )
    with zipfile.ZipFile(io.BytesIO(fixture_response_bytes(response))) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        assert workbook.find("s:sheets/s:sheet", ns).attrib["name"] == "Monthly rates"
        assert sheet.find(".//s:c[@r='A2']/s:is/s:t", ns).text == "2020-01"
        assert sheet.find(".//s:c[@r='B2']/s:v", ns).text == "1.5"
