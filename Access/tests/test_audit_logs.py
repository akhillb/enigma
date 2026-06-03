import pytest
import datetime
from django.contrib.auth.models import User as DjangoUser
from django.urls import reverse
from django.test import RequestFactory, Client
from django.core.exceptions import ValidationError
from Access.models import User, UserAccessMapping, GroupAccessMapping, MembershipV2, GroupV2, AccessV2, UserIdentity
from Access.views import audit_logs
from Access import views_helper

@pytest.fixture
def db_setup(db):
    # 1. Create Django Auth Users and Custom Users
    # Admin/Ops user
    admin_auth = DjangoUser.objects.create_superuser(username="admin", email="admin@test.com", password="pwd")
    admin_user = User.objects.get(user=admin_auth)
    admin_user.name = "Admin User"
    admin_user.is_ops = True
    admin_user.save()

    # Regular user
    reg_auth = DjangoUser.objects.create_user(username="regular", email="regular@test.com", password="pwd")
    reg_user = User.objects.get(user=reg_auth)
    reg_user.name = "Regular User"
    reg_user.is_ops = False
    reg_user.save()

    # 2. Create AccessV2 instances
    access_1 = AccessV2.objects.create(access_tag="ssh", access_label={"host": "production"})
    access_2 = AccessV2.objects.create(access_tag="db", access_label={"database": "users"})

    # 3. Create GroupV2
    group = GroupV2.objects.create(group_id="test-grp-id", name="dev-group", description="Devs", status="Approved")

    # 4. Create UserIdentity for regular user
    identity = UserIdentity.objects.create(user=reg_user, access_tag="ssh", status="Active", identity={"username": "reg"})

    # 5. Create audit data across three models
    # UserAccessMapping
    user_mapping = UserAccessMapping.objects.create(
        request_id="user-req-1",
        user_identity=identity,
        access=access_1,
        approver_1=admin_user,
        request_reason="Needs SSH access",
        access_type="Individual",
        status="Pending"
    )
    user_mapping.status = "Approved"
    user_mapping.save()

    # GroupAccessMapping
    group_mapping = GroupAccessMapping.objects.create(
        request_id="group-req-1",
        group=group,
        requested_by=reg_user,
        access=access_2,
        approver_1=admin_user,
        request_reason="Needs database access for group",
        status="Approved"
    )

    # MembershipV2
    membership = MembershipV2.objects.create(
        membership_id="membership-1",
        user=reg_user,
        group=group,
        requested_by=admin_user,
        status="Pending",
        reason="Join request",
        approver=admin_user
    )

    return {
        'admin_user': admin_auth,
        'reg_user': reg_auth,
        'user_mapping': user_mapping,
        'group_mapping': group_mapping,
        'membership': membership,
    }

@pytest.mark.django_db
def test_get_audit_log_filters():
    factory = RequestFactory()
    
    # 1. Valid parameters
    req = factory.get('/access/auditLogs', {
        'user': 'regular',
        'accessTag': 'ssh',
        'status': 'Approved',
        'recordType': 'userAccess',
        'dateFrom': '2026-06-01',
        'dateTo': '2026-06-03'
    })
    filters = views_helper.get_audit_log_filters(req)
    assert filters['user'] == 'regular'
    assert filters['accessTag'] == 'ssh'
    assert filters['status'] == 'Approved'
    assert filters['recordType'] == 'userAccess'
    assert filters['dateFrom'] == datetime.date(2026, 6, 1)
    assert filters['dateTo'] == datetime.date(2026, 6, 3)

    # 2. Malformed dateFrom
    req_bad_from = factory.get('/access/auditLogs', {'dateFrom': 'invalid-date'})
    with pytest.raises(ValidationError) as excinfo:
        views_helper.get_audit_log_filters(req_bad_from)
    assert "dateFrom" in str(excinfo.value)

    # 3. Malformed dateTo
    req_bad_to = factory.get('/access/auditLogs', {'dateTo': 'invalid-date'})
    with pytest.raises(ValidationError) as excinfo:
        views_helper.get_audit_log_filters(req_bad_to)
    assert "dateTo" in str(excinfo.value)

@pytest.mark.django_db
def test_get_audit_log_entries(db_setup):
    # 1. Empty filters returns all records sorted descending
    entries = views_helper.get_audit_log_entries({})
    assert len(entries) == 3
    # Check that sorting is descending by requested_on
    assert entries[0]['requested_on'] >= entries[1]['requested_on']
    assert entries[1]['requested_on'] >= entries[2]['requested_on']

    # 2. Filter by user (icontains on regular user)
    entries_user = views_helper.get_audit_log_entries({'user': 'regular'})
    assert len(entries_user) >= 2

    # 3. Filter by accessTag 'ssh' (ignores membership, matches userAccess)
    entries_tag = views_helper.get_audit_log_entries({'accessTag': 'ssh'})
    assert len(entries_tag) == 1
    assert entries_tag[0]['record_type'] == 'User Access'

    # 4. Filter by recordType 'membership'
    entries_type = views_helper.get_audit_log_entries({'recordType': 'membership'})
    assert len(entries_type) == 1
    assert entries_type[0]['record_type'] == 'Membership'

    # 5. Filter by status 'Pending'
    entries_status = views_helper.get_audit_log_entries({'status': 'Pending'})
    assert len(entries_status) == 1
    assert entries_status[0]['record_type'] == 'Membership'

@pytest.mark.django_db
def test_audit_logs_permissions(db_setup):
    client = Client()
    url = reverse('auditLogs')

    # Anonymous user should be redirected to login
    response = client.get(url)
    assert response.status_code == 302
    assert 'login' in response.url

    # Non-ops user should get 403 Forbidden
    client.force_login(db_setup['reg_user'], backend='django.contrib.auth.backends.ModelBackend')
    response_non_ops = client.get(url)
    assert response_non_ops.status_code == 403

    # Ops user should get 200 OK
    client.force_login(db_setup['admin_user'], backend='django.contrib.auth.backends.ModelBackend')
    response_ops = client.get(url)
    assert response_ops.status_code == 200
    assert 'EnigmaOps/auditLogs.html' in [t.name for t in response_ops.templates]

@pytest.mark.django_db
def test_audit_logs_view_json(db_setup):
    client = Client()
    client.force_login(db_setup['admin_user'], backend='django.contrib.auth.backends.ModelBackend')
    url = reverse('auditLogs')

    # JSON response type
    response = client.get(url, {'responseType': 'json'})
    assert response.status_code == 200
    data = response.json()
    assert 'dataList' in data
    assert len(data['dataList']) == 3
    assert data['current_page'] == 1
    assert data['last_page'] == 1
    assert data['total_count'] == 3

    # Malformed date in query should return 400 JsonResponse
    response_err = client.get(url, {'responseType': 'json', 'dateFrom': 'invalid'})
    assert response_err.status_code == 400
    assert 'error' in response_err.json()

@pytest.mark.django_db
def test_audit_logs_view_csv(db_setup):
    client = Client()
    client.force_login(db_setup['admin_user'], backend='django.contrib.auth.backends.ModelBackend')
    url = reverse('auditLogs')

    response = client.get(url, {'responseType': 'csv'})
    assert response.status_code == 200
    assert response['Content-Type'] == 'text/csv'
    assert 'attachment; filename="AuditLogs-' in response['Content-Disposition']
    content = response.content.decode('utf-8')
    lines = content.strip().split('\r\n')
    assert len(lines) == 4 # Header + 3 entries
    assert lines[0] == "RecordType,User,Access,Status,RequestedOn,UpdatedOn,Actors,Reason"
