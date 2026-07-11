import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from ic_env_guard.enrollment.protocol import (
    EnrollmentProtocolError,
    EnrollmentResponse,
    encode_response,
    parse_request,
)
from ic_env_guard.systemd.cli import build_runtime_parser

REQUEST = (
    b'{"protocol":"manager-enrollment.v1",'
    b'"manager_id":"2b576727-4f36-4f08-b90b-e8cbe98ebc80",'
    b'"enrollment_id":"01J2W4ABCDEFGHJKMNPQRSTVWX"}'
)


@pytest.mark.contract
def test_enrollment_request_is_exact_and_bounded():
    request = parse_request(REQUEST)
    assert request.protocol == "manager-enrollment.v1"
    assert request.manager_id == UUID("2b576727-4f36-4f08-b90b-e8cbe98ebc80")

    with pytest.raises(EnrollmentProtocolError, match="stdin exceeds 4096 bytes"):
        parse_request(b"x" * 4097)
    with pytest.raises(EnrollmentProtocolError, match="invalid enrollment request"):
        parse_request(b"not-json")


@pytest.mark.contract
@pytest.mark.parametrize(
    "payload",
    [
        {"protocol": "manager-enrollment.v2"},
        {"extra": True},
        {"manager_id": "2B576727-4F36-4F08-B90B-E8CBE98EBC80"},
        {"enrollment_id": "not-a-ulid"},
    ],
)
def test_enrollment_request_rejects_protocol_extensions(payload):
    values = json.loads(REQUEST)
    values.update(payload)
    with pytest.raises((EnrollmentProtocolError, ValidationError)):
        parse_request(json.dumps(values).encode())


@pytest.mark.contract
def test_enrollment_response_is_one_compact_bounded_json_line():
    response = EnrollmentResponse(
        protocol="manager-enrollment.v1",
        instance_id=UUID("a670d8f8-6074-4d7e-a118-15f445a25d72"),
        credential_id=UUID("77f962a4-f9ab-426a-84e3-5606982fa37f"),
        token="pending-token",
        expires_at=datetime(2026, 7, 11, 10, 10, tzinfo=UTC),
    )
    encoded = encode_response(response)

    assert encoded.endswith(b"\n")
    assert encoded.count(b"\n") == 1
    assert b" " not in encoded
    assert len(encoded) <= 8192
    assert json.loads(encoded)["expires_at"] == "2026-07-11T10:10:00Z"

    with pytest.raises(EnrollmentProtocolError, match="exceeds 8192 bytes"):
        encode_response(response.model_copy(update={"token": "x" * 8192}))


@pytest.mark.contract
def test_fixed_agent_helper_cli_accepts_no_parameters():
    parser = build_runtime_parser()
    args = parser.parse_args(["agent", "enroll-manager"])
    assert args.command == "agent"
    assert args.agent_command == "enroll-manager"

    for option in ("--url", "--ssh-option", "--identity", "--token", "--command"):
        with pytest.raises(SystemExit):
            parser.parse_args(["agent", "enroll-manager", option, "unsafe"])
