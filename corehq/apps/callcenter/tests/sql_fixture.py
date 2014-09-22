import uuid
import sqlalchemy
from sqlalchemy import *
from django.conf import settings
from sqlalchemy.engine.url import make_url
from datetime import date, timedelta, datetime
from corehq.apps.sofabed.models import FormData, CaseData

metadata = sqlalchemy.MetaData()


def get_table(mapping):
    return Table(mapping.table_name, metadata, extend_existing=True, *[c.sql_column for c in mapping.columns])


def get_formdata(days_ago, domain, user_id, xmlns=None, duration=1):
    now = datetime.now()
    return FormData(
        doc_type='XFormInstance',
        domain=domain,
        user_id=user_id,
        time_end=now - timedelta(days=days_ago),
        received_on=now,
        instance_id=uuid.uuid4(),
        time_start=now,
        duration=duration*1000,  # convert to ms
        xmlns=xmlns
    )


def get_casedata(domain, days_ago, case_id, user_id, owner_id, case_type, close):
    now = datetime.now()
    date_ago = now - timedelta(days=days_ago)
    return CaseData(
        case_id=case_id,
        doc_type='CommCareCase',
        type=case_type,
        domain=domain,
        owner_id=owner_id,
        user_id=user_id,
        opened_on=date_ago,
        modified_on=now,
        closed=close,
        closed_on=(date_ago if close else None)
    )
    return case


def add_case_action(case):
    case.actions.create(
        index=1,
        action_type='update',
        date=case.opened_on,
        user_id=case.user_id,
    )


def load_data(domain, form_user_id, case_user_id=None, case_owner_id=None):
    form_data = [
        get_formdata(0, domain, form_user_id),
        get_formdata(3, domain, form_user_id),
        get_formdata(7, domain, form_user_id),
        get_formdata(8, domain, form_user_id),
        get_formdata(9, domain, form_user_id),
        get_formdata(11, domain, form_user_id),
        get_formdata(14, domain, form_user_id),
        get_formdata(15, domain, form_user_id),
    ]

    case_user_id = case_user_id or form_user_id
    case_owner_id = case_owner_id or case_user_id
    case_data = [
        get_casedata(domain, 0, '1', case_user_id, case_owner_id, 'person', False),
        get_casedata(domain, 10, '2', case_user_id, case_owner_id, 'person', False),
        get_casedata(domain, 29, '3', case_user_id, case_owner_id, 'person', True),
        get_casedata(domain, 30, '4', case_user_id, case_owner_id, 'person', True),
        get_casedata(domain, 31, '5', case_user_id, case_owner_id, 'dog', True),
        get_casedata(domain, 45, '6', case_user_id, case_owner_id, 'dog', False),
        get_casedata(domain, 55, '7', case_user_id, case_owner_id, 'dog', False),
        get_casedata(domain, 56, '8', case_user_id, case_owner_id, 'dog', True),
        get_casedata(domain, 59, '9', case_user_id, case_owner_id, 'dog', False),
    ]

    FormData.objects.bulk_create(form_data)
    CaseData.objects.bulk_create(case_data)

    for case in case_data:
        add_case_action(case)


def load_custom_data(domain, user_id, xmlns):
    form_data = [
        get_formdata(0, domain, user_id, xmlns=xmlns, duration=3),
        get_formdata(1, domain, user_id, xmlns=xmlns, duration=2),
        get_formdata(3, domain, user_id, xmlns=xmlns, duration=4),
        get_formdata(7, domain, user_id, xmlns=xmlns, duration=3),
        get_formdata(8, domain, user_id, xmlns=xmlns, duration=4),
        get_formdata(13, domain, user_id, xmlns=xmlns, duration=5),
        get_formdata(14, domain, user_id, xmlns=xmlns, duration=2),
        get_formdata(17, domain, user_id, xmlns=xmlns, duration=1),
        get_formdata(29, domain, user_id, xmlns=xmlns, duration=12),
        get_formdata(30, domain, user_id, xmlns=xmlns, duration=1),
    ]

    FormData.objects.bulk_create(form_data)


def clear_data():
    FormData.objects.all().delete()
    CaseData.objects.all().delete()