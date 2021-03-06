from __future__ import absolute_import

import pprint
from collections import defaultdict
from datetime import datetime, timedelta
import hashlib

from alembic.autogenerate.api import compare_metadata
from django.conf import settings
from kafka.util import kafka_bytestring
import six

from corehq.apps.change_feed.consumer.feed import KafkaChangeFeed, KafkaCheckpointEventHandler
from corehq.apps.hqwebapp.tasks import send_mail_async
from corehq.apps.userreports.const import (
    KAFKA_TOPICS, UCR_ES_BACKEND, UCR_SQL_BACKEND, UCR_LABORATORY_BACKEND, UCR_ES_PRIMARY
)
from corehq.apps.userreports.data_source_providers import DynamicDataSourceProvider, StaticDataSourceProvider
from corehq.apps.userreports.exceptions import TableRebuildError, StaleRebuildError
from corehq.apps.userreports.models import AsyncIndicator
from corehq.apps.userreports.specs import EvaluationContext
from corehq.apps.userreports.sql import metadata
from corehq.apps.userreports.tasks import rebuild_indicators
from corehq.apps.userreports.util import get_indicator_adapter, get_backend_id
from corehq.sql_db.connections import connection_manager
from corehq.util.soft_assert import soft_assert
from fluff.signals import (
    apply_index_changes,
    get_migration_context,
    get_tables_with_index_changes,
    get_tables_to_rebuild,
    reformat_alembic_diffs
)
from pillowtop.checkpoints.manager import PillowCheckpoint
from pillowtop.logger import pillow_logging
from pillowtop.pillow.interface import ConstructedPillow
from pillowtop.processors import PillowProcessor
from pillowtop.utils import ensure_matched_revisions, ensure_document_exists

REBUILD_CHECK_INTERVAL = 60 * 60  # in seconds
LONG_UCR_LOGGING_THRESHOLD = 0.2
LONG_UCR_SOFT_ASSERT_THRESHOLD = 5
_slow_ucr_assert = soft_assert('{}@{}'.format('jemord', 'dimagi.com'))


class PillowConfigError(Exception):
    pass


def time_ucr_process_change(method):
    def timed(*args, **kw):
        ts = datetime.now()
        result = method(*args, **kw)
        te = datetime.now()
        seconds = (te - ts).total_seconds()
        if seconds > LONG_UCR_LOGGING_THRESHOLD:
            table = args[1]
            doc = args[2]
            log_message = u"UCR data source {} on doc_id {} took {} seconds to process".format(
                table.config._id, doc['_id'], seconds
            )
            pillow_logging.warning(log_message)
            if seconds > LONG_UCR_SOFT_ASSERT_THRESHOLD:
                email_message = u"UCR data source {} is taking too long to process".format(
                    table.config._id
                )
                _slow_ucr_assert(False, email_message)
        return result
    return timed


def _filter_by_hash(configs, ucr_division):
    ucr_start = ucr_division[0]
    ucr_end = ucr_division[-1]
    filtered_configs = []
    for config in configs:
        table_hash = hashlib.md5(config.table_id).hexdigest()[0]
        if ucr_start <= table_hash <= ucr_end:
            filtered_configs.append(config)
    return filtered_configs


class ConfigurableReportTableManagerMixin(object):

    def __init__(self, data_source_provider, auto_repopulate_tables=False, ucr_division=None,
                 include_ucrs=None, exclude_ucrs=None):
        """Initializes the processor for UCRs

        Keyword Arguments:
        ucr_division -- two hexadecimal digits that are used to determine a subset of UCR
                        datasources to process. The second digit should be higher than the
                        first
        include_ucrs -- list of ucr 'table_ids' to be included in this processor
        exclude_ucrs -- list of ucr 'table_ids' to be excluded in this processor
        """
        self.bootstrapped = False
        self.last_bootstrapped = datetime.utcnow()
        self.data_source_provider = data_source_provider
        self.auto_repopulate_tables = auto_repopulate_tables
        self.ucr_division = ucr_division
        self.include_ucrs = include_ucrs
        self.exclude_ucrs = exclude_ucrs
        if self.include_ucrs and self.ucr_division:
            raise PillowConfigError("You can't have include_ucrs and ucr_division")

    def get_all_configs(self):
        return self.data_source_provider.get_data_sources()

    def get_filtered_configs(self, configs=None):
        configs = configs or self.get_all_configs()

        if self.exclude_ucrs:
            configs = [config for config in configs if config.table_id not in self.exclude_ucrs]

        if self.include_ucrs:
            configs = [config for config in configs if config.table_id in self.include_ucrs]
        elif self.ucr_division:
            configs = _filter_by_hash(configs, self.ucr_division)

        return configs

    def needs_bootstrap(self):
        return (
            not self.bootstrapped
            or datetime.utcnow() - self.last_bootstrapped > timedelta(seconds=REBUILD_CHECK_INTERVAL)
        )

    def bootstrap_if_needed(self):
        if self.needs_bootstrap():
            self.bootstrap()

    def bootstrap(self, configs=None):
        configs = self.get_filtered_configs(configs)
        if not configs:
            pillow_logging.warning("UCR pillow has no configs to process")

        self.table_adapters_by_domain = defaultdict(list)

        for config in configs:
            self.table_adapters_by_domain[config.domain].append(
                get_indicator_adapter(config, can_handle_laboratory=True)
            )

        self.rebuild_tables_if_necessary()
        self.bootstrapped = True
        self.last_bootstrapped = datetime.utcnow()

    def _tables_by_engine_id(self, engine_ids):
        return [
            adapter
            for adapter_list in self.table_adapters_by_domain.values()
            for adapter in adapter_list
            if get_backend_id(adapter.config, can_handle_laboratory=True) in engine_ids
        ]

    def rebuild_tables_if_necessary(self):
        sql_supported_backends = [UCR_SQL_BACKEND, UCR_LABORATORY_BACKEND, UCR_ES_PRIMARY]
        es_supported_backends = [UCR_ES_BACKEND, UCR_LABORATORY_BACKEND, UCR_ES_PRIMARY]
        self._rebuild_sql_tables(self._tables_by_engine_id(sql_supported_backends))
        self._rebuild_es_tables(self._tables_by_engine_id(es_supported_backends))

    def _rebuild_sql_tables(self, adapters):
        # todo move this code to sql adapter rebuild_if_necessary
        tables_by_engine = defaultdict(dict)
        for adapter in adapters:
            sql_adapter = get_indicator_adapter(adapter.config)
            tables_by_engine[sql_adapter.engine_id][sql_adapter.get_table().name] = sql_adapter

        _assert = soft_assert(notify_admins=True)
        _notify_rebuild = lambda msg, obj: _assert(False, msg, obj)

        for engine_id, table_map in tables_by_engine.items():
            engine = connection_manager.get_engine(engine_id)
            table_names = list(table_map)
            with engine.begin() as connection:
                migration_context = get_migration_context(connection, table_names)
                raw_diffs = compare_metadata(migration_context, metadata)
                diffs = reformat_alembic_diffs(raw_diffs)

            tables_to_rebuild = get_tables_to_rebuild(diffs, table_names)
            for table_name in tables_to_rebuild:
                sql_adapter = table_map[table_name]
                if not sql_adapter.config.is_static:
                    try:
                        self.rebuild_table(sql_adapter)
                    except TableRebuildError as e:
                        _notify_rebuild(six.text_type(e), sql_adapter.config.to_json())
                else:
                    try:
                        if sql_adapter.config.get_id in ("static-enikshay-episode", "static-np-migration-3-episode"):

                            msg = """
                            ID:
                            {table_id}

                            RAW DIFFS:
                            {raw_diffs}

                            REFORMATTED DIFFS:
                            {reformatted_diffs}
                            """.format(
                                table_id=sql_adapter.config.get_id,
                                raw_diffs=pprint.pformat(raw_diffs, indent=2, width=40),
                                reformatted_diffs=pprint.pformat(diffs, indent=2, width=40),
                            )

                            send_mail_async(
                                subject="UCR Data source rebuild",
                                message=msg,
                                from_email=settings.DEFAULT_FROM_EMAIL,
                                recipient_list=["ncarnahan" + "{}".format("@") + "dimagi.com"],
                            )
                    except:
                        # Don't break task for this debugging code
                        pass
                    self.rebuild_table(sql_adapter)

            tables_with_index_changes = get_tables_with_index_changes(diffs, table_names)
            tables_with_index_changes -= tables_to_rebuild
            apply_index_changes(engine, raw_diffs, tables_with_index_changes)

    def _rebuild_es_tables(self, adapters):
        # note unlike sql rebuilds this doesn't rebuild the indicators
        for adapter in adapters:
            adapter.rebuild_table_if_necessary()

    def rebuild_table(self, adapter):
        if adapter.config.get_id in ("static-enikshay-episode", "static-np-migration-3-episode"):
            # https://manage.dimagi.com/default.asp?252832
            return
        config = adapter.config
        if not config.is_static:
            latest_rev = config.get_db().get_rev(config._id)
            if config._rev != latest_rev:
                raise StaleRebuildError('Tried to rebuild a stale table ({})! Ignoring...'.format(config))
        adapter.rebuild_table()
        if self.auto_repopulate_tables:
            rebuild_indicators.delay(adapter.config.get_id)


class ConfigurableReportPillowProcessor(ConfigurableReportTableManagerMixin, PillowProcessor):

    @time_ucr_process_change
    def _save_doc_to_table(self, table, doc, eval_context):
        # best effort will swallow errors in the table
        table.best_effort_save(doc, eval_context)

    def process_change(self, pillow_instance, change):
        self.bootstrap_if_needed()
        if change.deleted:
            # we don't currently support hard-deletions at all.
            # we may want to change this at some later date but seem ok for now.
            # see https://github.com/dimagi/commcare-hq/pull/6944 for rationale
            return

        domain = change.metadata.domain
        if not domain:
            # if no domain we won't save to any UCR table
            return

        async_tables = []
        doc = change.get_document()
        ensure_document_exists(change)
        ensure_matched_revisions(change)

        if doc is None:
            return

        eval_context = EvaluationContext(doc)
        for table in self.table_adapters_by_domain[domain]:
            if table.config.filter(doc):
                if table.run_asynchronous:
                    async_tables.append(table.config._id)
                else:
                    self._save_doc_to_table(table, doc, eval_context)
                    eval_context.reset_iteration()
            elif table.config.deleted_filter(doc):
                table.delete(doc)

        if async_tables:
            AsyncIndicator.update_indicators(change, async_tables)


class ConfigurableReportKafkaPillow(ConstructedPillow):
    # the only reason this is a class is to avoid exposing processors
    # for tests to be able to call bootstrap on it.
    # we could easily remove the class and push all the stuff in __init__ to
    # get_kafka_ucr_pillow below if we wanted.

    # don't retry errors until we figure out how to distinguish between
    # doc save errors and data source config errors
    retry_errors = False

    def __init__(self, processor, pillow_name, topics):
        change_feed = KafkaChangeFeed(topics, group_id=pillow_name)
        checkpoint = PillowCheckpoint(pillow_name, change_feed.sequence_format)
        event_handler = KafkaCheckpointEventHandler(
            checkpoint=checkpoint, checkpoint_frequency=1000, change_feed=change_feed
        )
        super(ConfigurableReportKafkaPillow, self).__init__(
            name=pillow_name,
            change_feed=change_feed,
            processor=processor,
            checkpoint=checkpoint,
            change_processed_event_handler=event_handler
        )
        # set by the superclass constructor
        assert self.processors is not None
        assert len(self.processors) == 1
        self._processor = self.processors[0]
        assert self._processor.bootstrapped is not None

    def bootstrap(self, configs=None):
        self._processor.bootstrap(configs)

    def rebuild_table(self, sql_adapter):
        self._processor.rebuild_table(sql_adapter)


def get_kafka_ucr_pillow(pillow_id='kafka-ucr-main', ucr_division=None,
                         include_ucrs=None, exclude_ucrs=None, topics=None,
                         **kwargs):
    topics = topics or KAFKA_TOPICS
    topics = [kafka_bytestring(t) for t in topics]
    return ConfigurableReportKafkaPillow(
        processor=ConfigurableReportPillowProcessor(
            data_source_provider=DynamicDataSourceProvider(),
            auto_repopulate_tables=False,
            ucr_division=ucr_division,
            include_ucrs=include_ucrs,
            exclude_ucrs=exclude_ucrs,
        ),
        pillow_name=pillow_id,
        topics=topics
    )


def get_kafka_ucr_static_pillow(pillow_id='kafka-ucr-static', ucr_division=None,
                                include_ucrs=None, exclude_ucrs=None, topics=None,
                                **kwargs):
    topics = topics or KAFKA_TOPICS
    topics = [kafka_bytestring(t) for t in topics]
    return ConfigurableReportKafkaPillow(
        processor=ConfigurableReportPillowProcessor(
            data_source_provider=StaticDataSourceProvider(),
            auto_repopulate_tables=True,
            ucr_division=ucr_division,
            include_ucrs=include_ucrs,
            exclude_ucrs=exclude_ucrs,
        ),
        pillow_name=pillow_id,
        topics=topics
    )
