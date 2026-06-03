import csv
import datetime

from django.db.models import Q
from django.http import HttpResponse

from Access.models import GroupAccessMapping, MembershipV2, UserAccessMapping

RECORD_TYPE_USER_ACCESS = "userAccess"
RECORD_TYPE_GROUP_ACCESS = "groupAccess"
RECORD_TYPE_MEMBERSHIP = "membership"

RECORD_TYPE_CHOICES = (
    (RECORD_TYPE_USER_ACCESS, "User Access"),
    (RECORD_TYPE_GROUP_ACCESS, "Group Access"),
    (RECORD_TYPE_MEMBERSHIP, "Membership"),
)

ALL_STATUSES = sorted(
    {choice[0] for choice in UserAccessMapping.STATUS_CHOICES}
    | {choice[0] for choice in GroupAccessMapping.STATUS_CHOICES}
    # MembershipV2 names its choices STATUS, not STATUS_CHOICES.
    | {choice[0] for choice in MembershipV2.STATUS}
)


class InvalidAuditFilterError(Exception):
    """Raised when audit log filter input is malformed."""


def _parse_date(value, param_name):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise InvalidAuditFilterError(
            "Invalid date for '%s'. Expected YYYY-MM-DD." % param_name
        )


def get_audit_log_filters(request):
    record_type = request.GET.get("recordType", "").strip()
    valid_record_types = {choice[0] for choice in RECORD_TYPE_CHOICES} | {""}
    if record_type not in valid_record_types:
        raise InvalidAuditFilterError("Invalid recordType '%s'." % record_type)

    status = request.GET.get("status", "").strip()
    if status and status not in ALL_STATUSES:
        raise InvalidAuditFilterError("Invalid status '%s'." % status)

    date_from = _parse_date(request.GET.get("dateFrom", "").strip(), "dateFrom")
    date_to = _parse_date(request.GET.get("dateTo", "").strip(), "dateTo")
    if date_from and date_to and date_from > date_to:
        raise InvalidAuditFilterError("dateFrom must not be after dateTo.")

    return {
        "user": request.GET.get("user", "").strip(),
        "access_tag": request.GET.get("accessTag", "").strip(),
        "status": status,
        "record_type": record_type,
        "date_from": date_from,
        "date_to": date_to,
    }


def _format_timestamp(value):
    return str(value)[:19] if value else ""


def _format_label(access_label):
    if not access_label:
        return ""
    return ", ".join(
        key + "-" + str(val).strip("[]")
        for key, val in access_label.items()
        if key != "keySecret"
    )


def _username(access_user):
    return access_user.user.username if access_user else ""


def _format_actors(*access_users):
    return ", ".join(filter(None, (_username(u) for u in access_users)))


def _normalize_user_access(mapping):
    label = _format_label(mapping.access.access_label)
    access = mapping.access.access_tag + (" (" + label + ")" if label else "")
    return {
        "record_type": "User Access",
        "user": mapping.user_identity.user.email if mapping.user_identity else "",
        "access": access,
        "status": mapping.status,
        "requested_on": _format_timestamp(mapping.requested_on),
        "updated_on": _format_timestamp(mapping.updated_on),
        "actors": _format_actors(
            mapping.approver_1, mapping.approver_2, mapping.revoker
        ),
        "reason": mapping.decline_reason or mapping.request_reason or "",
    }


def _normalize_group_access(mapping):
    return {
        "record_type": "Group Access",
        "user": mapping.requested_by.email if mapping.requested_by else "",
        "access": mapping.group.name + " -> " + mapping.access.access_tag,
        "status": mapping.status,
        "requested_on": _format_timestamp(mapping.requested_on),
        "updated_on": _format_timestamp(mapping.updated_on),
        "actors": _format_actors(
            mapping.approver_1, mapping.approver_2, mapping.revoker
        ),
        "reason": mapping.decline_reason or mapping.request_reason or "",
    }


def _normalize_membership(membership):
    return {
        "record_type": "Membership",
        "user": membership.user.email if membership.user else "",
        "access": membership.group.name,
        "status": membership.status,
        "requested_on": _format_timestamp(membership.requested_on),
        "updated_on": _format_timestamp(membership.updated_on),
        "actors": _format_actors(membership.approver),
        "reason": membership.decline_reason or membership.reason or "",
    }


def _apply_date_and_status_filters(queryset, filters):
    if filters["status"]:
        queryset = queryset.filter(status=filters["status"])
    if filters["date_from"]:
        queryset = queryset.filter(requested_on__date__gte=filters["date_from"])
    if filters["date_to"]:
        queryset = queryset.filter(requested_on__date__lte=filters["date_to"])
    return queryset


def _user_access_entries(filters):
    queryset = UserAccessMapping.objects.select_related(
        "user_identity__user__user",
        "access",
        "approver_1__user",
        "approver_2__user",
        "revoker__user",
    )
    if filters["user"]:
        queryset = queryset.filter(
            Q(user_identity__user__user__username__icontains=filters["user"])
            | Q(user_identity__user__email__icontains=filters["user"])
        )
    if filters["access_tag"]:
        queryset = queryset.filter(
            access__access_tag__icontains=filters["access_tag"]
        )
    queryset = _apply_date_and_status_filters(queryset, filters)
    return [_normalize_user_access(mapping) for mapping in queryset]


def _group_access_entries(filters):
    queryset = GroupAccessMapping.objects.select_related(
        "group",
        "access",
        "requested_by",
        "approver_1__user",
        "approver_2__user",
        "revoker__user",
    )
    if filters["user"]:
        queryset = queryset.filter(
            Q(requested_by__user__username__icontains=filters["user"])
            | Q(requested_by__email__icontains=filters["user"])
        )
    if filters["access_tag"]:
        queryset = queryset.filter(
            access__access_tag__icontains=filters["access_tag"]
        )
    queryset = _apply_date_and_status_filters(queryset, filters)
    return [_normalize_group_access(mapping) for mapping in queryset]


def _membership_entries(filters):
    queryset = MembershipV2.objects.select_related(
        "user", "group", "approver__user"
    )
    if filters["user"]:
        queryset = queryset.filter(
            Q(user__user__username__icontains=filters["user"])
            | Q(user__email__icontains=filters["user"])
        )
    queryset = _apply_date_and_status_filters(queryset, filters)
    return [_normalize_membership(membership) for membership in queryset]


def get_audit_log_entries(filters):
    record_type = filters["record_type"]
    entries = []
    if record_type in ("", RECORD_TYPE_USER_ACCESS):
        entries.extend(_user_access_entries(filters))
    if record_type in ("", RECORD_TYPE_GROUP_ACCESS):
        entries.extend(_group_access_entries(filters))
    # Memberships carry no access tag, so an accessTag filter excludes them.
    if record_type in ("", RECORD_TYPE_MEMBERSHIP) and not filters["access_tag"]:
        entries.extend(_membership_entries(filters))
    # Timestamps are ISO-like strings: lexicographic sort == chronological.
    entries.sort(key=lambda entry: entry["requested_on"], reverse=True)
    return entries
