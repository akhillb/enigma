from django.shortcuts import render
from django.http import HttpResponse
import datetime
import logging
import traceback

import csv
from . import helpers as helper
from .models import UserAccessMapping, GroupAccessMapping, MembershipV2
from django.core.exceptions import ValidationError
from django.db import models
from bootprocess import general
from Access.background_task_manager import background_task, accept_request

logger = logging.getLogger(__name__)


def generate_user_mappings(user, group, membership):
    group_mappings = group.get_approved_accesses()

    user_mappings_list = []
    for group_mapping in group_mappings:
        access = group_mapping.access
        approver_1 = group_mapping.approver_1
        approver_2 = group_mapping.approver_2
        membership_id = membership.membership_id
        base_datetime_prefix = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
        reason = (
            "Added to group for request "
            + membership_id
            + " - "
            + membership.reason
            + " - "
            + group_mapping.request_reason
        )
        request_id = (
            user.user.username + "-" + access.access_tag + "-" + base_datetime_prefix
        )
        similar_id_mappings = list(
            UserAccessMapping.objects.filter(
                request_id__icontains=request_id
            ).values_list("request_id", flat=True)
        )

        request_id = get_next_index(
            request_id=request_id, similar_id_mappings=similar_id_mappings
        )

        user_identity = user.get_or_create_active_identity(access.access_tag)

        if user_identity and not user_identity.has_approved_access(access=access):
            user_mapping = user_identity.create_access_mapping(
                request_id=request_id,
                access=access,
                approver_1=approver_1,
                approver_2=approver_2,
                reason=reason,
                access_type="Group",
            )
            user_mappings_list.append(user_mapping)
    return user_mappings_list


def execute_group_access(user_mappings_list):
    for mapping in user_mappings_list:
        user = mapping.user_identity.user
        if user.current_state() == "active":
            if "other" in mapping.request_id:
                decline_group_other_access(mapping)
            else:
                accept_request(mapping)
                logger.debug("Successful group access grant for " + mapping.request_id)
        else:
            mapping.decline_access(decline_reason="User is not active")
            logger.debug(
                "Skipping group access grant for user "
                + user.user.username
                + " as user is not active"
            )


def decline_group_other_access(access_mapping):
    user = access_mapping.user
    access_mapping.decline_access(
        decline_reason="Auto decline for 'Other Access'. Please replace this with correct access."
    )
    logger.debug(
        "Skipping group access grant for user "
        + user.user.username
        + " for request_id "
        + access_mapping.request_id
        + " as it is 'Other Access'"
    )


def get_next_index(request_id, similar_id_mappings):
    idx = 0
    while True:
        new_request_id = request_id + "_" + str(idx)
        if new_request_id not in similar_id_mappings:
            return new_request_id
        idx += 1


def render_error_message(request, log_message, user_message, user_message_description):
    logger.error(log_message)
    return render(
        request,
        "EnigmaOps/accessStatus.html",
        {
            "error": {
                "error_msg": user_message,
                "msg": user_message_description,
            }
        },
    )


def get_filters_for_access_list(request):
    filters = {}
    if "accessTag" in request.GET:
        filters["access__access_tag__icontains"] = request.GET.get("accessTag")
    if "accessTagExact" in request.GET:
        filters["access__access_tag"] = request.GET.get("accessTagExact")
    if "status" in request.GET:
        filters["status__icontains"] = request.GET.get("status")
    if "type" in request.GET:
        filters["access_type__icontains"] = request.GET.get("type")
    return filters


def prepare_datalist(paginator, record_date):
    data_list = []
    for each_access_request in paginator:
        if (
            record_date is not None
            and record_date != str(each_access_request.updated_on)[:10]
        ):
            continue
        access_details = get_generic_user_access_mapping(each_access_request)
        data_list.append(access_details)
    return data_list


def gen_all_user_access_list_csv(data_list):
    logger.debug("Processing CSV response")
    response = HttpResponse(content_type="text/csv")
    filename = (
        "AccessList-"
        + str(datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S"))
        + ".csv"
    )
    response["Content-Disposition"] = 'attachment; filename="' + filename + '"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "User",
            "AccessType",
            "Access",
            "AccessStatus",
            "RequestDate",
            "Approver",
            "GrantOwner",
            "RevokeOwner",
            "Type",
        ]
    )
    for data in data_list:
        access_status = data["status"]
        if len(data["revoker"]) > 0:
            access_status += " by - " + data["revoker"]
        writer.writerow(
            [
                data["user"],
                data["access_desc"],
                (", ".join(data["access_label"])),
                access_status,
                data["requested_on"],
                data["approver_1"],
                data["grantOwner"],
                data["revokeOwner"],
                data["access_type"],
            ]
        )
    return response


def get_generic_user_access_mapping(user_access_mapping):
    access_module = helper.get_available_access_module_from_tag(
        user_access_mapping.access.access_tag
    )
    if access_module:
        access_details = user_access_mapping.getAccessRequestDetails(access_module)
    logger.debug("Generic access generated: " + str(access_details))
    return access_details


def get_audit_log_filters(request):
    filters = {}
    
    user_query = request.GET.get('user', '').strip()
    if user_query:
        filters['user'] = user_query
        
    access_tag_query = request.GET.get('accessTag', '').strip()
    if access_tag_query:
        filters['accessTag'] = access_tag_query
        
    status = request.GET.get('status', '').strip()
    if status:
        filters['status'] = status
        
    record_type = request.GET.get('recordType', '').strip()
    if record_type:
        filters['recordType'] = record_type
        
    date_from_str = request.GET.get('dateFrom', '').strip()
    if date_from_str:
        try:
            filters['dateFrom'] = datetime.datetime.strptime(date_from_str, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError("Invalid dateFrom format. Expected YYYY-MM-DD.")
            
    date_to_str = request.GET.get('dateTo', '').strip()
    if date_to_str:
        try:
            filters['dateTo'] = datetime.datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError("Invalid dateTo format. Expected YYYY-MM-DD.")
            
    return filters


def get_audit_log_entries(filters):
    record_type = filters.get('recordType')
    entries = []
    
    # 1. UserAccessMapping
    if not record_type or record_type == 'userAccess':
        queryset = UserAccessMapping.objects.all().select_related(
            'user_identity__user__user',
            'access',
            'approver_1__user',
            'approver_2__user',
            'revoker__user'
        )
        if 'user' in filters:
            u = filters['user']
            queryset = queryset.filter(
                models.Q(user_identity__user__user__username__icontains=u) |
                models.Q(user_identity__user__email__icontains=u)
            )
        if 'accessTag' in filters:
            t = filters['accessTag']
            queryset = queryset.filter(access__access_tag__icontains=t)
        if 'status' in filters:
            queryset = queryset.filter(status__iexact=filters['status'])
        if 'dateFrom' in filters:
            queryset = queryset.filter(requested_on__date__gte=filters['dateFrom'])
        if 'dateTo' in filters:
            queryset = queryset.filter(requested_on__date__lte=filters['dateTo'])
            
        for obj in queryset:
            user_obj = obj.user_identity.user if obj.user_identity else None
            user_str = f"{user_obj.user.username} ({user_obj.email})" if user_obj and user_obj.user else (user_obj.email if user_obj else "")
            
            access_label_str = ", ".join(f"{k}: {v}" for k, v in obj.access.access_label.items() if k != 'keySecret')
            access_str = f"{obj.access.access_tag} ({access_label_str})" if access_label_str else obj.access.access_tag
            
            actors = []
            if obj.approver_1:
                actors.append(f"Approver 1: {obj.approver_1.user.username}")
            if obj.approver_2:
                actors.append(f"Approver 2: {obj.approver_2.user.username}")
            if obj.revoker:
                actors.append(f"Revoker: {obj.revoker.user.username}")
            actors_str = ", ".join(actors)
            
            reason = obj.request_reason
            if obj.decline_reason:
                reason += f" (Declined: {obj.decline_reason})"
            elif obj.fail_reason:
                reason += f" (Failed: {obj.fail_reason})"
                
            entries.append({
                'record_type': 'User Access',
                'user': user_str,
                'access': access_str,
                'status': obj.status,
                'requested_on': obj.requested_on,
                'updated_on': obj.updated_on,
                'actors': actors_str,
                'reason': reason,
            })
            
    # 2. GroupAccessMapping
    if not record_type or record_type == 'groupAccess':
        queryset = GroupAccessMapping.objects.all().select_related(
            'group',
            'requested_by__user',
            'access',
            'approver_1__user',
            'approver_2__user',
            'revoker__user'
        )
        if 'user' in filters:
            u = filters['user']
            queryset = queryset.filter(
                models.Q(requested_by__user__username__icontains=u) |
                models.Q(requested_by__email__icontains=u)
            )
        if 'accessTag' in filters:
            t = filters['accessTag']
            queryset = queryset.filter(access__access_tag__icontains=t)
        if 'status' in filters:
            queryset = queryset.filter(status__iexact=filters['status'])
        if 'dateFrom' in filters:
            queryset = queryset.filter(requested_on__date__gte=filters['dateFrom'])
        if 'dateTo' in filters:
            queryset = queryset.filter(requested_on__date__lte=filters['dateTo'])
            
        for obj in queryset:
            user_obj = obj.requested_by
            user_str = f"{user_obj.user.username} ({user_obj.email})" if user_obj and user_obj.user else (user_obj.email if user_obj else "")
            
            access_str = f"{obj.group.name} - {obj.access.access_tag}"
            
            actors = []
            if obj.approver_1:
                actors.append(f"Approver 1: {obj.approver_1.user.username}")
            if obj.approver_2:
                actors.append(f"Approver 2: {obj.approver_2.user.username}")
            if obj.revoker:
                actors.append(f"Revoker: {obj.revoker.user.username}")
            actors_str = ", ".join(actors)
            
            reason = obj.request_reason
            if obj.decline_reason:
                reason += f" (Declined: {obj.decline_reason})"
                
            entries.append({
                'record_type': 'Group Access',
                'user': user_str,
                'access': access_str,
                'status': obj.status,
                'requested_on': obj.requested_on,
                'updated_on': obj.updated_on,
                'actors': actors_str,
                'reason': reason,
            })
            
    # 3. MembershipV2
    if (not record_type or record_type == 'membership') and 'accessTag' not in filters:
        queryset = MembershipV2.objects.all().select_related(
            'user__user',
            'group',
            'requested_by__user',
            'approver__user'
        )
        if 'user' in filters:
            u = filters['user']
            queryset = queryset.filter(
                models.Q(user__user__username__icontains=u) |
                models.Q(user__email__icontains=u)
            )
        if 'status' in filters:
            queryset = queryset.filter(status__iexact=filters['status'])
        if 'dateFrom' in filters:
            queryset = queryset.filter(requested_on__date__gte=filters['dateFrom'])
        if 'dateTo' in filters:
            queryset = queryset.filter(requested_on__date__lte=filters['dateTo'])
            
        for obj in queryset:
            user_obj = obj.user
            user_str = f"{user_obj.user.username} ({user_obj.email})" if user_obj and user_obj.user else (user_obj.email if user_obj else "")
            
            access_str = obj.group.name
            
            actors = []
            if obj.approver:
                actors.append(f"Approver: {obj.approver.user.username}")
            actors_str = ", ".join(actors)
            
            reason = obj.reason or ""
            if obj.decline_reason:
                reason += f" (Declined: {obj.decline_reason})"
                
            entries.append({
                'record_type': 'Membership',
                'user': user_str,
                'access': access_str,
                'status': obj.status,
                'requested_on': obj.requested_on,
                'updated_on': obj.updated_on,
                'actors': actors_str,
                'reason': reason,
            })
            
    entries.sort(key=lambda x: x['requested_on'], reverse=True)
    return entries


def gen_audit_logs_csv(data_list):
    logger.debug("Processing Audit Logs CSV response")
    response = HttpResponse(content_type="text/csv")
    filename = (
        "AuditLogs-"
        + str(datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S"))
        + ".csv"
    )
    response["Content-Disposition"] = 'attachment; filename="' + filename + '"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "RecordType",
            "User",
            "Access",
            "Status",
            "RequestedOn",
            "UpdatedOn",
            "Actors",
            "Reason",
        ]
    )
    for data in data_list:
        requested_on_str = data['requested_on'].strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(data['requested_on'], datetime.datetime) else str(data['requested_on'])
        updated_on_str = data['updated_on'].strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(data['updated_on'], datetime.datetime) else str(data['updated_on'])
        writer.writerow(
            [
                data["record_type"],
                data["user"],
                data["access"],
                data["status"],
                requested_on_str,
                updated_on_str,
                data["actors"],
                data["reason"],
            ]
        )
    return response
