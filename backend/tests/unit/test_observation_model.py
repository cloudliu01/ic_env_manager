from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ic_env_guard.observations.models import ObservationInput

BASE = {
    "namespace": "eda",
    "name": "license_server_alive",
    "kind": "gauge",
    "value": 1,
    "status": "ok",
    "observed_at": "2026-07-11T10:00:00Z",
    "ttl_seconds": 120,
}


@pytest.mark.unit
def test_identity_is_independent_of_label_order():
    left = ObservationInput.model_validate(
        {**BASE, "labels": {"vendor": "synopsys", "server": "a"}}
    )
    right = ObservationInput.model_validate(
        {**BASE, "labels": {"server": "a", "vendor": "synopsys"}}
    )

    assert left.identity_key() == right.identity_key()
    assert len(left.identity_key()) == 64
    assert left.identity_key().islower()


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "EDA"),
        ("namespace", "a" * 64),
        ("name", "1invalid"),
        ("name", "a" * 128),
        ("kind", "histogram"),
        ("status", "healthy"),
        ("unit", "x" * 33),
        ("ttl_seconds", 0),
        ("ttl_seconds", 604801),
    ],
)
def test_scalar_rules(field, value):
    with pytest.raises(ValidationError):
        ObservationInput.model_validate({**BASE, field: value})


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, True, "1", float("nan"), float("inf")])
def test_gauge_requires_finite_numeric_value(value):
    payload = {**BASE, "value": value}
    with pytest.raises(ValidationError):
        ObservationInput.model_validate(payload)


@pytest.mark.unit
def test_status_may_omit_value_and_aware_time_is_normalized_to_utc():
    model = ObservationInput.model_validate(
        {
            **BASE,
            "kind": "status",
            "value": None,
            "observed_at": "2026-07-11T11:00:00+01:00",
        }
    )

    assert model.value is None
    assert model.observed_at == datetime(2026, 7, 11, 10, tzinfo=UTC)


@pytest.mark.unit
@pytest.mark.parametrize(
    "details",
    [
        {"nested": {"one": {"two": {"three": {"too_deep": True}}}}},
        {"blob": "x" * 16385},
        {"key": float("nan")},
        {"key": b"binary"},
        {"x" * 65: True},
        {"value": "é" * 2049},
    ],
)
def test_details_limits(details):
    with pytest.raises(ValidationError):
        ObservationInput.model_validate({**BASE, "details": details})


@pytest.mark.unit
def test_details_limits_use_compact_utf8_bytes_and_allow_depth_four():
    model = ObservationInput.model_validate(
        {
            **BASE,
            "details": {"one": {"two": {"three": "ok"}}, "utf8": "é" * 100},
        }
    )

    assert model.details["utf8"] == "é" * 100


@pytest.mark.unit
def test_details_depth_counts_containers_not_scalar_leaves():
    allowed = ObservationInput.model_validate(
        {**BASE, "details": {"nested": [[[1]]]}}
    )
    assert allowed.details == {"nested": [[[1]]]}

    with pytest.raises(ValidationError):
        ObservationInput.model_validate(
            {**BASE, "details": {"nested": [[[[1]]]]}}
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"message": "é" * 1025},
        {"labels": {f"label_{index}": "x" for index in range(17)}},
        {"labels": {"Bad": "x"}},
        {"labels": {"key": "é" * 65}},
        {"observed_at": "2026-07-11T10:00:00"},
        {"producer_id": "caller"},
    ],
)
def test_byte_limits_aware_time_and_caller_producer_are_enforced(overrides):
    with pytest.raises(ValidationError):
        ObservationInput.model_validate({**BASE, **overrides})
