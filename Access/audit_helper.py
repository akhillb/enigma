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


def _email(related):
    """Return related.email or '' for a possibly-None related User."""
    return getattr(related, "email", "") or "" if related is not None else ""


def _access_tag(access):
    return getattr(access, "access_tag", "") or "" if access is not None else ""


def _group_name(group):
    return getattr(group, "name", "") or "" if group is not None else ""


def _first_reason(*values):
    """Return the first non-empty reason string."""
    for value in values:
        if value:
            return value
    return ""


def map_user_access(obj):
    identity_user = getattr(obj.user_identity, "user", None) if obj.user_identity else None
    return {
        "timestamp": obj.updated_on,
        "actor": _email(identity_user),
        "action": "User Access",
        "status": obj.status,
        "resource": _access_tag(obj.access),
        "reason": _first_reason(obj.decline_reason, obj.fail_reason, obj.request_reason),
        "approver": _email(obj.approver_1),
        "source_type": "user_access",
    }


def map_membership(obj):
    return {
        "timestamp": obj.updated_on,
        "actor": _email(obj.user),
        "action": "Group Membership",
        "status": obj.status,
        "resource": _group_name(obj.group),
        "reason": _first_reason(obj.decline_reason, obj.reason),
        "approver": _email(obj.approver),
        "source_type": "group_membership",
    }


def map_group(obj):
    # GroupV2's resource is its own `name` field (not a related object).
    return {
        "timestamp": obj.updated_on,
        "actor": _email(obj.requester),
        "action": "Group Lifecycle",
        "status": obj.status,
        "resource": getattr(obj, "name", "") or "",
        "reason": _first_reason(obj.decline_reason),
        "approver": _email(obj.approver),
        "source_type": "group_lifecycle",
    }


def map_group_access(obj):
    group = _group_name(obj.group)
    access = _access_tag(obj.access)
    resource = " / ".join([part for part in (group, access) if part])
    return {
        "timestamp": obj.updated_on,
        "actor": _email(obj.requested_by),
        "action": "Group Access",
        "status": obj.status,
        "resource": resource,
        "reason": _first_reason(obj.decline_reason, obj.request_reason),
        "approver": _email(obj.approver_1),
        "source_type": "group_access",
    }


# Each source declares the ORM paths used to push filters to the DB.
SOURCES = (
    {
        "model": UserAccessMapping,
        "timestamp_field": "updated_on",
        "actor_path": "user_identity__user__email",
        "resource_path": "access__access_tag",
        "mapper": map_user_access,
    },
    {
        "model": MembershipV2,
        "timestamp_field": "updated_on",
        "actor_path": "user__email",
        "resource_path": "group__name",
        "mapper": map_membership,
    },
    {
        "model": GroupV2,
        "timestamp_field": "updated_on",
        "actor_path": "requester__email",
        "resource_path": "name",
        "mapper": map_group,
    },
    {
        "model": GroupAccessMapping,
        "timestamp_field": "updated_on",
        "actor_path": "requested_by__email",
        "resource_path": "group__name",
        "mapper": map_group_access,
    },
)


def _orm_filters(source, filters):
    orm = {}
    ts = source["timestamp_field"]
    if filters["date_from"]:
        orm[ts + "__date__gte"] = filters["date_from"]
    if filters["date_to"]:
        orm[ts + "__date__lte"] = filters["date_to"]
    if filters["actor"]:
        orm[source["actor_path"] + "__icontains"] = filters["actor"]
    if filters["status"]:
        orm["status"] = filters["status"]
    if filters["resource"]:
        orm[source["resource_path"] + "__icontains"] = filters["resource"]
    return orm


def build_audit_entries(filters):
    """Query every source with pushed-down filters, normalize, merge, sort desc."""
    entries = []
    for source in SOURCES:
        orm = _orm_filters(source, filters)
        for obj in source["model"].objects.filter(**orm):
            entries.append(source["mapper"](obj))
    entries.sort(key=lambda entry: entry["timestamp"], reverse=True)
    return entries
