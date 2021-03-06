from collections import Counter

from casexml.apps.case.models import CommCareCase
from corehq.apps import es
from corehq.apps.domain.dbaccessors import get_doc_count_in_domain_by_type, get_doc_ids_in_domain_by_type
from corehq.apps.es import aggregations
from corehq.form_processor.backends.sql.dbaccessors import doc_type_to_state
from corehq.form_processor.models import XFormInstanceSQL, CommCareCaseSQL
from corehq.form_processor.utils.general import should_use_sql_backend
from corehq.sql_db.config import get_sql_db_aliases_in_use
from couchforms.models import all_known_formlike_doc_types


def get_es_counts_by_doc_type(domain, es_indices=None):
    es_indices = es_indices or (es.CaseES, es.FormES, es.UserES, es.AppES, es.LedgerES, es.GroupES)
    counter = Counter()
    for es_query in es_indices:
        counter += get_index_counts_by_domain_doc_type(es_query, domain)

    return counter


def get_index_counts_by_domain_doc_type(es_query_class, domain):
    """
    :param es_query_class: Subclass of ``HQESQuery``
    :param domain: Domain name to filter on
    :returns: Counter of document counts per doc_type in the ES Index
    """
    return Counter(
        es_query_class()
        .remove_default_filters()
        .filter(es.users.domain(domain))
        .terms_aggregation('doc_type', 'doc_type')
        .size(0)
        .run()
        .aggregations.doc_type.counts_by_bucket()
    )


def get_es_user_counts_by_doc_type(domain):
    agg = aggregations.TermsAggregation('doc_type', 'doc_type').aggregation(
        aggregations.TermsAggregation('base_doc', 'base_doc')
    )
    doc_type_buckets = (
        es.UserES()
        .remove_default_filters()
        .filter(es.users.domain(domain))
        .aggregation(agg)
        .size(0)
        .run()
        .aggregations.doc_type.buckets_dict
    )
    counts = Counter()
    for doc_type, bucket in doc_type_buckets.items():
        for base_doc, count in bucket.base_doc.counts_by_bucket().items():
            deleted = base_doc.endswith('deleted')
            if deleted:
                doc_type += '-Deleted'
            counts[doc_type] = count

    return counts


def get_primary_db_form_counts(domain):
    if should_use_sql_backend(domain):
        return _get_sql_forms_by_doc_type(domain)
    else:
        return _get_couch_forms_by_doc_type(domain)


def get_primary_db_form_ids(domain, doc_type):
    if should_use_sql_backend(domain):
        return get_sql_form_ids(domain, doc_type)
    else:
        return set(get_doc_ids_in_domain_by_type(domain, doc_type, CommCareCase.get_db()))


def get_primary_db_case_counts(domain):
    if should_use_sql_backend(domain):
        return _get_sql_cases_by_doc_type(domain)
    else:
        return _get_couch_cases_by_doc_type(domain)


def get_primary_db_case_ids(domain, doc_type):
    if should_use_sql_backend(domain):
        return get_sql_case_ids(domain, doc_type)
    else:
        return set(get_doc_ids_in_domain_by_type(domain, doc_type, CommCareCase.get_db()))


def _get_couch_forms_by_doc_type(domain):
    return _get_couch_doc_counts(CommCareCase.get_db(), domain, all_known_formlike_doc_types())


def _get_couch_cases_by_doc_type(domain):
    return _get_couch_doc_counts(CommCareCase.get_db(), domain, ('CommCareCase', 'CommCareCase-Deleted'))


def _get_couch_doc_counts(couch_db, domain, doc_types):
    counter = Counter()
    for doc_type in doc_types:
        count = get_doc_count_in_domain_by_type(domain, doc_type, couch_db)
        counter.update({doc_type: count})
    return counter


def _get_sql_forms_by_doc_type(domain):
    counter = Counter()
    for db_alias in get_sql_db_aliases_in_use():
        queryset = XFormInstanceSQL.objects.using(db_alias).filter(domain=domain)
        for doc_type, state in doc_type_to_state.items():
            counter[doc_type] += queryset.filter(state=state).count()

        where_clause = 'state & {0} = {0}'.format(XFormInstanceSQL.DELETED)
        counter['XFormInstance-Deleted'] += queryset.extra(where=[where_clause]).count()

    return counter


def _get_sql_cases_by_doc_type(domain):
    counter = Counter()
    for db_alias in get_sql_db_aliases_in_use():
        queryset = CommCareCaseSQL.objects.using(db_alias).filter(domain=domain)
        counter['CommCareCase'] += queryset.filter(deleted=False).count()
        counter['CommCareCase-Deleted'] += queryset.filter(deleted=True).count()

    return counter


def get_sql_case_ids(domain, doc_type):
    sql_ids = set()
    deleted = doc_type == 'CommCareCase-Deleted'
    for db_alias in get_sql_db_aliases_in_use():
        queryset = CommCareCaseSQL.objects.using(db_alias) \
            .filter(domain=domain, deleted=deleted).values_list('case_id', flat=True)
        sql_ids.update(list(queryset))
    return sql_ids


def get_sql_form_ids(domain, doc_type):
    sql_ids = set()
    state = doc_type_to_state[doc_type]
    for db_alias in get_sql_db_aliases_in_use():
        queryset = XFormInstanceSQL.objects.using(db_alias) \
            .filter(domain=domain, state=state).values_list('form_id', flat=True)
        sql_ids.update(list(queryset))
    return sql_ids


def get_es_case_ids(domain, doc_type):
    return _get_es_doc_ids(es.CaseES, domain, doc_type)


def get_es_form_ids(domain, doc_type):
    return _get_es_doc_ids(es.FormES, domain, doc_type)


def _get_es_doc_ids(es_query_class, domain, doc_type):
    return set(
        es_query_class()
        .remove_default_filters()
        .filter(es.filters.domain(domain))
        .filter(es.filters.OR(
            es.filters.doc_type(doc_type),
            es.filters.doc_type(doc_type.lower()),
        )).exclude_source().scroll()
    )


def get_es_user_ids(domain, doc_type):
    return set(
        es.UserES()
        .remove_default_filters()
        .filter(es.users.domain(domain))
        .filter(es.filters.doc_type(doc_type))
        .filter(_get_user_base_doc_filter(doc_type))
        .get_ids()
    )


def _get_user_base_doc_filter(doc_type):
    deleted = 'Deleted' in doc_type
    if deleted:
        doc_type = doc_type[:-1]

    if doc_type == 'CommCareUser':
        return {"term": {
            "base_doc": "couchuser-deleted" if deleted else "couchuser"
        }}
