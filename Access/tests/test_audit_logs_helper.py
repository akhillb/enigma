import datetime

from Access import audit_helper


def test_parse_filters_happy_path():
    params = {
        "dateFrom": "2026-01-01",
        "dateTo": "2026-02-01",
        "actor": "  alice@example.com ",
        "status": "Approved",
        "resource": "  aws-prod ",
    }
    result = audit_helper.parse_filters(params)
    assert result == {
        "date_from": datetime.date(2026, 1, 1),
        "date_to": datetime.date(2026, 2, 1),
        "actor": "alice@example.com",
        "status": "Approved",
        "resource": "aws-prod",
    }


def test_parse_filters_empty_params_yield_none():
    result = audit_helper.parse_filters({})
    assert result == {
        "date_from": None,
        "date_to": None,
        "actor": None,
        "status": None,
        "resource": None,
    }


def test_parse_filters_invalid_date_dropped():
    result = audit_helper.parse_filters({"dateFrom": "not-a-date", "dateTo": "13/2026"})
    assert result["date_from"] is None
    assert result["date_to"] is None


def test_parse_filters_unknown_status_dropped():
    result = audit_helper.parse_filters({"status": "Bogus"})
    assert result["status"] is None


def test_known_statuses_is_sorted_unique():
    assert audit_helper.KNOWN_STATUSES == sorted(set(audit_helper.KNOWN_STATUSES))
    assert "Approved" in audit_helper.KNOWN_STATUSES
    assert "GrantFailed" in audit_helper.KNOWN_STATUSES
