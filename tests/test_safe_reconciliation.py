from types import SimpleNamespace as NS
from unittest.mock import Mock

from lake_openmetadata_ingestion.config import MetadataConfig
from lake_openmetadata_ingestion.metadata_sync import MetadataSync
from lake_openmetadata_ingestion.lake_client import TableRow
from lake_openmetadata_ingestion.om_client import OMError


def setup_sync():
    cfg = NS(metadata=NS(exclude_databases=[], catalogs=['catalog'], mark_deleted=True),
             stages=NS(enabled=False), service=NS(name='lake'))
    db = Mock()
    db.databases.return_value = ['db']
    db.tables.return_value = [TableRow('catalog', 'db', 'still_here', 'BASE TABLE', 'FUSE', '', False, False, None)]
    db.columns.return_value = []
    om = Mock()
    om.list_databases.return_value = [{'name': 'catalog', 'fullyQualifiedName': 'lake.catalog'}]
    om.list_schemas.return_value = [{'name': 'db', 'fullyQualifiedName': 'lake.catalog.db', 'id': 'schema'}]
    om.list_tables.return_value = [{'name': 'still_here', 'id': 'existing'}, {'name': 'gone', 'id': 'obsolete'}]
    return MetadataSync(cfg, db, om), om


def test_failed_write_never_deletes_live_or_unseen_entities():
    sync, om = setup_sync()
    om.upsert_table.side_effect = OMError(503, 'transient test error')
    stats = sync.run()
    assert stats.failed == 1 and stats.deleted == 0
    om.soft_delete.assert_not_called()


def test_successful_opt_in_reconciliation_still_deletes_absent_entity():
    sync, om = setup_sync()
    stats = sync.run()
    assert stats.failed == 0 and stats.deleted == 1
    om.soft_delete.assert_called_once_with('tables', 'obsolete')


def test_deletion_requires_explicit_opt_in():
    assert MetadataConfig().mark_deleted is False
