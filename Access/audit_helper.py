"""Read-only audit log aggregation over existing access-lifecycle models.

Aggregates UserAccessMapping, MembershipV2, GroupV2 and GroupAccessMapping
into a unified, filterable, sortable stream. No schema changes, no writes.
"""
import csv
import datetime
import logging

from django.http import HttpResponse

from Access.models import (
    GroupAccessMapping,
    GroupV2,
    MembershipV2,
    UserAccessMapping,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 25

# Union of every status declared on the four source models, sorted + unique.
KNOWN_STATUSES = sorted(
    {
        "Pending", "SecondaryPending", "Processing", "Approved", "GrantFailed",
        "Declined", "Offboarding", "ProcessingRevoke", "RevokeFailed", "Revoked",
        "Deprecated", "Inactive",
    }
)


def _parse_date(value):
    """Return a date for an ISO YYYY-MM-DD string, else None (never raises)."""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _validate_status(value):
    """Return value if it is a known status, else None."""
    value = (value or "").strip()
    return value if value in KNOWN_STATUSES else None


def parse_filters(get_params):
    """Normalize raw GET params into a typed filter dict; invalid values drop to None."""
    return {
        "date_from": _parse_date(get_params.get("dateFrom")),
        "date_to": _parse_date(get_params.get("dateTo")),
        "actor": (get_params.get("actor") or "").strip() or None,
        "status": _validate_status(get_params.get("status")),
        "resource": (get_params.get("resource") or "").strip() or None,
    }
