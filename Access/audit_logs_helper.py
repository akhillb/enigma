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
