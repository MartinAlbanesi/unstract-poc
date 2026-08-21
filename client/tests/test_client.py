import json

import pytest
import requests

from client import (
    ApiConnectionError,
    ExtractionFailedError,
    InvalidInputError,
    _parse_classifier_output,
    classify_document,
    load_config,
    main,
    run_auto,
    run_extraction,
    validate_input_file,
)

ENV = {
    "UNSTRACT_BASE_URL": "http://frontend.unstract.localhost",
    "UNSTRACT_ORG": "mock_org",
    "FACTURA_API_NAME": "factura",
    "FACTURA_API_KEY": "test-factura-key",
    "REMITO_API_NAME": "remito",
    "REMITO_API_KEY": "test-remito-key",
    "CARTA_DE_PORTE_API_NAME": "carta-porte",
    "CARTA_DE_PORTE_API_KEY": "test-carta-key",
    "CLASSIFIER_API_NAME": "document-classifier",
    "CLASSIFIER_API_KEY": "test-classifier-key",
}

POST_URL = "http://frontend.unstract.localhost/deployment/api/mock_org/factura/"
CLASSIFIER_POST_URL = (
    "http://frontend.unstract.localhost/deployment/api/mock_org/document-classifier/"
)


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4 fake content")
    return str(path)


def _extraction_output():
    return {
        "fecha_emision": "2026-03-26",
        "total": 1512.5,
    }


def _completed_result_list():
    return [
        {
            "file": "sample.pdf",
            "result": {"output": _extraction_output()},
            "error": None,
        }
    ]


def _prompt_studio_wrapped_result_list(fenced: bool = False):
    """Mirrors the real API shape: output = {'<type>-json': '<json string>'},
    optionally fenced in a ```json code block."""
    inner = json.dumps(_extraction_output())
    value = f"```json\n{inner}\n```" if fenced else inner
    return [
        {
            "file": "sample.pdf",
            "result": {"output": {"factura-json": value}},
            "error": None,
        }
    ]


def _classifier_envelopes(*overrides):
    """Default single-envelope classifier response; pass dicts to override
    or add entries, e.g. _classifier_envelopes({"documento_valido": False})."""
    if not overrides:
        overrides = ({},)
    base = {
        "pagina_origen": 1,
        "documento_valido": True,
        "region": [0.0, 0.0, 1.0, 1.0],
        "tipo_documento": "factura",
        "tipo_comprobante": "FACTURA A",
        "observaciones": "",
    }
    return [{**base, **override} for override in overrides]


def _classifier_completed_result_list(envelopes, fenced: bool = False):
    """Mirrors the real classifier API shape: output =
    {'document-classifier-json': '<JSON ARRAY string>'}."""
    inner = json.dumps(envelopes)
    value = f"```json\n{inner}\n```" if fenced else inner
    return [
        {
            "file": "sample.pdf",
            "result": {"output": {"document-classifier-json": value}},
            "error": None,
        }
    ]


# --- load_config ---------------------------------------------------------


def test_load_config_resolves_from_env():
    config = load_config("factura", env=ENV)
    assert config["base_url"] == "http://frontend.unstract.localhost"
    assert config["org"] == "mock_org"
    assert config["api_name"] == "factura"
    assert config["api_key"] == "test-factura-key"


def test_load_config_maps_carta_de_porte_hyphenated_api_name():
    config = load_config("carta_de_porte", env=ENV)
    assert config["api_name"] == "carta-porte"


def test_load_config_missing_key_raises_invalid_input():
    partial_env = dict(ENV)
    del partial_env["FACTURA_API_KEY"]
    with pytest.raises(InvalidInputError):
        load_config("factura", env=partial_env)


# --- validate_input_file --------------------------------------------------


def test_validate_input_file_missing():
    with pytest.raises(InvalidInputError):
        validate_input_file("does/not/exist.pdf")


def test_validate_input_file_empty(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(InvalidInputError):
        validate_input_file(str(path))


def test_validate_input_file_ok(sample_pdf):
    path = validate_input_file(sample_pdf)
    assert path.exists()


# --- run_extraction: happy paths -----------------------------------------


def test_run_extraction_completes_immediately_on_post(requests_mock, sample_pdf):
    """POST response already reports COMPLETED (e.g. cached/instant run)."""
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-1",
                "execution_status": "COMPLETED",
                "result": _completed_result_list(),
            }
        },
        status_code=200,
    )
    output = run_extraction("factura", sample_pdf, env=ENV)
    assert output == _extraction_output()


def test_run_extraction_unwraps_prompt_studio_json_string(requests_mock, sample_pdf):
    """Real Prompt Studio deployments return output as {'<type>-json': '<json
    string>'} rather than a nested object — must be transparently unwrapped."""
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-6",
                "execution_status": "COMPLETED",
                "result": _prompt_studio_wrapped_result_list(fenced=False),
            }
        },
        status_code=200,
    )
    output = run_extraction("factura", sample_pdf, env=ENV)
    assert output == _extraction_output()


def test_run_extraction_unwraps_markdown_fenced_json_string(requests_mock, sample_pdf):
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-7",
                "execution_status": "COMPLETED",
                "result": _prompt_studio_wrapped_result_list(fenced=True),
            }
        },
        status_code=200,
    )
    output = run_extraction("factura", sample_pdf, env=ENV)
    assert output == _extraction_output()


def test_run_extraction_polls_until_completed(requests_mock, sample_pdf, monkeypatch):
    monkeypatch.setattr("client.POLL_INTERVAL_SECONDS", 0)
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-2",
                "execution_status": "PENDING",
                "status_api": POST_URL + "?execution_id=exec-2",
            }
        },
        status_code=200,
    )
    requests_mock.get(
        POST_URL,
        [
            {"json": {"status": "EXECUTING", "message": None}, "status_code": 422},
            {
                "json": {"status": "COMPLETED", "message": _completed_result_list()},
                "status_code": 200,
            },
        ],
    )
    output = run_extraction("factura", sample_pdf, env=ENV, timeout=30)
    assert output == _extraction_output()


# --- failure modes (CL-3) -------------------------------------------------


def test_run_extraction_invalid_file_exits_1(requests_mock):
    with pytest.raises(InvalidInputError) as exc_info:
        run_extraction("factura", "missing.pdf", env=ENV)
    assert exc_info.value.exit_code == 1


def test_run_extraction_connection_error_exits_2(sample_pdf, requests_mock):
    requests_mock.post(POST_URL, exc=requests.exceptions.ConnectionError("refused"))
    with pytest.raises(ApiConnectionError) as exc_info:
        run_extraction("factura", sample_pdf, env=ENV)
    assert exc_info.value.exit_code == 2


def test_run_extraction_http_error_exits_2(sample_pdf, requests_mock):
    requests_mock.post(POST_URL, status_code=401, text="unauthorized")
    with pytest.raises(ApiConnectionError) as exc_info:
        run_extraction("factura", sample_pdf, env=ENV)
    assert exc_info.value.exit_code == 2


def test_run_extraction_error_status_exits_3(requests_mock, sample_pdf):
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-3",
                "execution_status": "ERROR",
                "error": "LLM adapter unreachable",
            }
        },
        status_code=200,
    )
    with pytest.raises(ExtractionFailedError) as exc_info:
        run_extraction("factura", sample_pdf, env=ENV)
    assert exc_info.value.exit_code == 3


def test_run_extraction_poll_error_status_exits_3(requests_mock, sample_pdf, monkeypatch):
    monkeypatch.setattr("client.POLL_INTERVAL_SECONDS", 0)
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-4",
                "execution_status": "PENDING",
            }
        },
        status_code=200,
    )
    requests_mock.get(
        POST_URL,
        json={"status": "ERROR", "message": "tool crashed"},
        status_code=422,
    )
    with pytest.raises(ExtractionFailedError) as exc_info:
        run_extraction("factura", sample_pdf, env=ENV, timeout=30)
    assert exc_info.value.exit_code == 3


def test_run_extraction_unknown_type_exits_1(sample_pdf):
    with pytest.raises(InvalidInputError):
        run_extraction("unknown_type", sample_pdf, env=ENV)


def test_run_extraction_rejects_classifier_as_extraction_type(sample_pdf):
    """"classifier" is routing-only and deliberately excluded from
    DOC_TYPES; run_extraction must keep rejecting it."""
    with pytest.raises(InvalidInputError):
        run_extraction("classifier", sample_pdf, env=ENV)


# --- _parse_classifier_output ----------------------------------------------


def test_parse_classifier_output_parses_array_string():
    envelopes = _classifier_envelopes()
    output = {"document-classifier-json": json.dumps(envelopes)}
    assert _parse_classifier_output(output) == envelopes


def test_parse_classifier_output_strips_markdown_fence():
    envelopes = _classifier_envelopes()
    fenced = f"```json\n{json.dumps(envelopes)}\n```"
    output = {"document-classifier-json": fenced}
    assert _parse_classifier_output(output) == envelopes


def test_parse_classifier_output_raises_on_invalid_json():
    output = {"document-classifier-json": "{not valid json"}
    with pytest.raises(ExtractionFailedError):
        _parse_classifier_output(output)


def test_parse_classifier_output_raises_on_non_array():
    # A JSON object (not an array) is not a valid classifier response shape.
    output = {"document-classifier-json": json.dumps({"tipo_documento": "factura"})}
    with pytest.raises(ExtractionFailedError):
        _parse_classifier_output(output)


def test_parse_classifier_output_raises_on_missing_field():
    with pytest.raises(ExtractionFailedError):
        _parse_classifier_output({"some-other-json": "[]"})


# --- classify_document -------------------------------------------------


def test_classify_document_returns_first_valid_tipo_documento(requests_mock, sample_pdf):
    envelopes = _classifier_envelopes(
        {"documento_valido": False, "tipo_documento": None},
        {"documento_valido": True, "tipo_documento": "remito", "tipo_comprobante": "REMITO"},
    )
    requests_mock.post(
        CLASSIFIER_POST_URL,
        json={
            "message": {
                "execution_id": "exec-classify-1",
                "execution_status": "COMPLETED",
                "result": _classifier_completed_result_list(envelopes),
            }
        },
        status_code=200,
    )
    tipo_documento = classify_document(sample_pdf, env=ENV)
    assert tipo_documento == "remito"


def test_classify_document_raises_when_no_entry_valid(requests_mock, sample_pdf):
    envelopes = _classifier_envelopes(
        {"documento_valido": False, "tipo_documento": None, "observaciones": "fuera de alcance"}
    )
    requests_mock.post(
        CLASSIFIER_POST_URL,
        json={
            "message": {
                "execution_id": "exec-classify-2",
                "execution_status": "COMPLETED",
                "result": _classifier_completed_result_list(envelopes),
            }
        },
        status_code=200,
    )
    with pytest.raises(ExtractionFailedError) as exc_info:
        classify_document(sample_pdf, env=ENV)
    assert exc_info.value.exit_code == 3


def test_classify_document_raises_on_unrecognized_tipo_documento(requests_mock, sample_pdf):
    envelopes = _classifier_envelopes(
        {"documento_valido": True, "tipo_documento": "not_a_real_type"}
    )
    requests_mock.post(
        CLASSIFIER_POST_URL,
        json={
            "message": {
                "execution_id": "exec-classify-3",
                "execution_status": "COMPLETED",
                "result": _classifier_completed_result_list(envelopes),
            }
        },
        status_code=200,
    )
    with pytest.raises(ExtractionFailedError) as exc_info:
        classify_document(sample_pdf, env=ENV)
    assert exc_info.value.exit_code == 3


# --- run_auto -------------------------------------------------------------


def test_run_auto_classifies_then_routes_to_matching_extraction(requests_mock, sample_pdf):
    envelopes = _classifier_envelopes({"tipo_documento": "remito", "tipo_comprobante": "REMITO"})
    requests_mock.post(
        CLASSIFIER_POST_URL,
        json={
            "message": {
                "execution_id": "exec-classify-4",
                "execution_status": "COMPLETED",
                "result": _classifier_completed_result_list(envelopes),
            }
        },
        status_code=200,
    )
    remito_url = "http://frontend.unstract.localhost/deployment/api/mock_org/remito/"
    requests_mock.post(
        remito_url,
        json={
            "message": {
                "execution_id": "exec-extract-1",
                "execution_status": "COMPLETED",
                "result": _completed_result_list(),
            }
        },
        status_code=200,
    )
    output = run_auto(sample_pdf, env=ENV)
    assert output == _extraction_output()
    # Both the classifier and the routed extraction endpoint were called.
    called_urls = [req.url for req in requests_mock.request_history]
    assert CLASSIFIER_POST_URL in called_urls
    assert remito_url in called_urls


def test_run_auto_propagates_classification_failure(requests_mock, sample_pdf):
    envelopes = _classifier_envelopes({"documento_valido": False, "tipo_documento": None})
    requests_mock.post(
        CLASSIFIER_POST_URL,
        json={
            "message": {
                "execution_id": "exec-classify-5",
                "execution_status": "COMPLETED",
                "result": _classifier_completed_result_list(envelopes),
            }
        },
        status_code=200,
    )
    with pytest.raises(ExtractionFailedError):
        run_auto(sample_pdf, env=ENV)


# --- main() CLI exit codes -------------------------------------------------


def test_main_exits_1_for_missing_file(capsys):
    exit_code = main(["--type", "factura", "--file", "missing.pdf"])
    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_main_exits_0_and_prints_json(requests_mock, sample_pdf, capsys, monkeypatch):
    monkeypatch.setenv("UNSTRACT_BASE_URL", ENV["UNSTRACT_BASE_URL"])
    monkeypatch.setenv("UNSTRACT_ORG", ENV["UNSTRACT_ORG"])
    monkeypatch.setenv("FACTURA_API_NAME", ENV["FACTURA_API_NAME"])
    monkeypatch.setenv("FACTURA_API_KEY", ENV["FACTURA_API_KEY"])
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-5",
                "execution_status": "COMPLETED",
                "result": _completed_result_list(),
            }
        },
        status_code=200,
    )
    exit_code = main(["--type", "factura", "--file", sample_pdf])
    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == _extraction_output()


def test_main_without_type_goes_through_auto_classify_path(
    requests_mock, sample_pdf, capsys, monkeypatch
):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    envelopes = _classifier_envelopes({"tipo_documento": "factura"})
    requests_mock.post(
        CLASSIFIER_POST_URL,
        json={
            "message": {
                "execution_id": "exec-main-auto-1",
                "execution_status": "COMPLETED",
                "result": _classifier_completed_result_list(envelopes),
            }
        },
        status_code=200,
    )
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-main-auto-2",
                "execution_status": "COMPLETED",
                "result": _completed_result_list(),
            }
        },
        status_code=200,
    )
    exit_code = main(["--file", sample_pdf])
    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == _extraction_output()
    called_urls = [req.url for req in requests_mock.request_history]
    assert CLASSIFIER_POST_URL in called_urls


def test_main_with_type_never_calls_classifier(requests_mock, sample_pdf, capsys, monkeypatch):
    """--type is a fast-path override: the classifier deployment must never
    be hit. Deliberately does NOT mock CLASSIFIER_POST_URL, so any accidental
    call to it raises requests_mock.NoMockAddress and fails this test."""
    monkeypatch.setenv("UNSTRACT_BASE_URL", ENV["UNSTRACT_BASE_URL"])
    monkeypatch.setenv("UNSTRACT_ORG", ENV["UNSTRACT_ORG"])
    monkeypatch.setenv("FACTURA_API_NAME", ENV["FACTURA_API_NAME"])
    monkeypatch.setenv("FACTURA_API_KEY", ENV["FACTURA_API_KEY"])
    requests_mock.post(
        POST_URL,
        json={
            "message": {
                "execution_id": "exec-main-typed-1",
                "execution_status": "COMPLETED",
                "result": _completed_result_list(),
            }
        },
        status_code=200,
    )
    exit_code = main(["--type", "factura", "--file", sample_pdf])
    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == _extraction_output()
    called_urls = [req.url for req in requests_mock.request_history]
    assert CLASSIFIER_POST_URL not in called_urls
