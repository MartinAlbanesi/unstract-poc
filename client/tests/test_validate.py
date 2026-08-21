import json
from pathlib import Path

import pytest

from validate import schema_path_for, validate_extraction

FIXTURES = Path(__file__).parent / "fixtures"
DOC_TYPES = ["factura", "remito", "carta_de_porte"]


def _load_fixture(name: str) -> dict:
    with open(FIXTURES / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_clean_fixture_is_valid(doc_type):
    data = _load_fixture(f"{doc_type}_clean")
    errors = validate_extraction(data, schema_path_for(doc_type))
    assert errors == []


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_noisy_ocr_values_do_not_fail_validation(doc_type):
    """CL-2: garbled string VALUES (OCR noise) must not produce errors as
    long as required keys are present with the right basic type."""
    data = _load_fixture(f"{doc_type}_noisy")
    errors = validate_extraction(data, schema_path_for(doc_type))
    assert errors == []


def test_missing_top_level_field_is_reported():
    data = _load_fixture("factura_clean")
    del data["total"]
    errors = validate_extraction(data, schema_path_for("factura"))
    assert any("total" in e and "missing" in e for e in errors)


def test_missing_nested_field_is_reported():
    data = _load_fixture("factura_clean")
    del data["emisor"]["cuit"]
    errors = validate_extraction(data, schema_path_for("factura"))
    assert any("emisor.cuit" in e for e in errors)


def test_wrong_type_is_reported():
    data = _load_fixture("factura_clean")
    data["total"] = "not-a-number"
    errors = validate_extraction(data, schema_path_for("factura"))
    assert any("total" in e and "float" in e for e in errors)


def test_int_accepted_where_float_expected():
    data = _load_fixture("factura_clean")
    data["total"] = 1512  # int instead of float — should be tolerated
    errors = validate_extraction(data, schema_path_for("factura"))
    assert errors == []


def test_list_item_missing_field_reported_with_index():
    data = _load_fixture("factura_clean")
    del data["detalle"][0]["cantidad"]
    errors = validate_extraction(data, schema_path_for("factura"))
    assert any("detalle[0].cantidad" in e for e in errors)


def test_wrong_type_for_list_field():
    data = _load_fixture("factura_clean")
    data["detalle"] = "not-a-list"
    errors = validate_extraction(data, schema_path_for("factura"))
    assert any("detalle" in e and "list" in e for e in errors)


def test_root_not_a_dict():
    errors = validate_extraction([], schema_path_for("factura"))
    assert len(errors) == 1
    assert "object" in errors[0]
