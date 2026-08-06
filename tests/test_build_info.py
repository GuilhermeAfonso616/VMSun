from app.core.build_info import (
    OPERATOR_API_VERSION,
    RECOMMENDED_OPERATOR_CLIENT_VERSION,
    SERVER_VERSION,
    build_info_payload,
    web_version_text,
)


def test_build_info_payload_exposes_operator_contract():
    payload = build_info_payload()

    assert payload["server_version"] == SERVER_VERSION
    assert payload["operator_api_version"] == OPERATOR_API_VERSION
    assert payload["recommended_operator_client_version"] == RECOMMENDED_OPERATOR_CLIENT_VERSION


def test_web_version_text_is_operator_friendly():
    assert web_version_text().startswith(f"Web v{SERVER_VERSION}")
