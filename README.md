# lake-openmetadata-ingestion

Pushes [TiDB Cloud Lake](https://www.pingcap.com/tidb-cloud/) metadata **and** data lineage into
[OpenMetadata](https://open-metadata.org) over the REST API. Works with any OpenMetadata 1.x/2.x
server as-is: no server plugin, no custom ingestion image.

## What it does

| TiDB Cloud Lake | OpenMetadata |
|---|---|
| service | `DatabaseService` of type `CustomDatabase` (`displayName` = TiDB Cloud Lake) |
| catalog (`SHOW CATALOGS`) | `Database` |
| database (`system.databases`) | `DatabaseSchema` |
| table / view (`system.tables`, `system.views`) | `Table` (`tableType` Regular/View/External/Transient/Iceberg, `schemaDefinition` = view SQL) |
| column (`system.columns.data_type`) | `Column` (type map in `lake_openmetadata_ingestion/types.py`) |
| named stage (`system.stages`, optional) | `Table` with `tableType=Stage` under a configurable schema |
| `system_history.lineage_history` row | table→table lineage edge with `columnsLineage`, `sqlQuery`, `source` = `ViewLineage` for `CREATE_VIEW`, else `QueryLineage` |

FQNs are `service.catalog.database.table`.

## Requirements

* Python ≥ 3.10; `pip install -e .`
* A TiDB Cloud Lake cluster with data lineage and history tables enabled, so that
  `system_history.lineage_history` exists.
* An OpenMetadata bot JWT. `init-bot` (below) creates a least-privilege bot; the built-in
  `ingestion-bot` token also works but is over-privileged.
* The Lake user needs `SELECT` on `system.*` and `system_history.lineage_history`.
* Lake session timezone must be UTC (the default); the watermark is compared as a naive
  timestamp literal.

## Usage

The CLI is installed as `lake-om` (alias: `lake-openmetadata-ingestion`).

```bash
cp config.example.yaml config.yaml          # edit; ${ENV_VAR} placeholders are expanded

# once, by an OM admin: creates bot + policy + role and prints the bot JWT
OM_ADMIN_EMAIL=admin@example.com OM_ADMIN_PASSWORD=... lake-om -c config.yaml init-bot
#   with SSO (no basic-auth login):  OM_ADMIN_TOKEN=<any admin JWT> lake-om init-bot
#   write to a file instead of stdout: --write-token /path/to/token   (mode 600)
export OM_JWT_TOKEN=...                     # the bot token

lake-om -c config.yaml init-service   # create/update the service (idempotent)
lake-om -c config.yaml metadata       # full sync of catalogs/databases/tables/columns
lake-om -c config.yaml lineage        # incremental; --full ignores the watermark
lake-om -c config.yaml all            # the three above
```

Schedule `metadata` (e.g. hourly) and `lineage` (e.g. every 5–15 min) with cron/Airflow. Run
`lineage --full` once after the first `metadata` sync and after any long outage.

The bot's policy allows `Create/ViewAll/EditAll/Delete` on `databaseService`, `database`,
`databaseSchema`, `table` only, and denies `EditDisplayName`. Re-running `init-bot` rotates the
token (the previous one stops working). Token lifetime: `bot.token_expiry` (default 90 days).

## Configuration

See `config.example.yaml` for every option. The ones that matter operationally:

| Key | Meaning |
|---|---|
| `lake.dsn` | Connection string of your Lake cluster, e.g. `lake://user:pass@<host>.tidbcloud.com:443/?warehouse=<wh>`. |
| `service.name` | OM service name; part of every FQN. Changing it means a fresh sync into a new service. |
| `metadata.catalogs` | Catalogs to sync (empty = all from `SHOW CATALOGS`). |
| `metadata.mark_deleted` | Soft-delete OM schemas/tables that disappeared from Lake (default `true`). |
| `lineage.state_file` | **Watermark file** — the only state this tool keeps. See below. |
| `lineage.lookback_seconds` | Overlap re-read before the watermark to absorb late `lineage_history` merges. |
| `lineage.prune_view_upstreams` | Remove OM upstream edges of a redefined view that Lake no longer reports. |
| `stages.enabled` | Also sync named stages and stage↔table lineage. |

### The watermark file (`lineage.state_file`)

Incremental `lineage` runs read rows with `updated_on > watermark - lookback_seconds` and, after a
successful run, store the batch's `max(updated_on)` in this JSON file. It is the *Lake* data
timestamp, not wall-clock time, so machine clocks do not matter.

* It must be on **persistent storage** and the scheduler must always run from the same path
  (or point `state_file` at a shared location). Losing it just means the next run is a full replay.
* Only one `lineage` process should use a given file at a time.
* Delete it (or run `lineage --full`) to replay everything, e.g. after re-creating the service.
* `metadata` and `init-*` never touch it.

### Lineage semantics

* Edges are written by name as recorded in `lineage_history`; if either endpoint does not exist in
  OM (404) the edge is skipped and logged. Run `metadata` before `lineage`.
* One table pair can have several `lineage_history` rows (one per distinct set of column mappings,
  e.g. two `INSERT`s filling different columns), but OM has a single edge per pair and its
  `PUT /lineage` **replaces** the edge's column lineage instead of merging. An incremental run
  therefore uses the new rows only to find *which* edges changed, then re-reads **all** rows of those
  edges from Lake and writes the union — mappings from earlier statements are not lost.
* One OM edge per table pair: `CREATE_VIEW` sets `source=ViewLineage`, otherwise `QueryLineage`;
  column mappings are unioned; `sqlQuery` is the most recent statement.
* A view's upstream edges come from its newest `CREATE VIEW` statement only; with
  `prune_view_upstreams` the tool also deletes OM upstream edges of that view which Lake no
  longer reports (only `ViewLineage`/`QueryLineage` edges — manually drawn edges are kept).
* Tables dropped in Lake are soft-deleted by `metadata` (`mark_deleted`), which hides their
  lineage in OM. Edges between two tables that both still exist are never deleted by this tool.

### Named stages (optional)

Lake stages are tenant-level (no catalog/database), while OM only has `Table` with
`tableType=Stage` living under a schema (the same model OM uses for Snowflake stages). With
`stages.enabled: true` every non-internal stage from `system.stages` is created as
`<service>.<stages.database>.<stages.schema>.<stage>` (description = type + URL + comment, no
columns), and stage endpoints in `lineage_history` map to that FQN, so `COPY INTO @stage FROM t`,
`COPY INTO t FROM @stage` and `CREATE TABLE t AS SELECT ... FROM @stage` all appear. Lake records
no column lineage for stage edges. `stages.schema` must not clash with a real database in
`stages.database`.

## Notes

* Nested `TUPLE` field names appear upper-cased in OM (`STRUCT` children `AGE`, `CITY` for
  `tuple(age int32, city string)`) because that is how `system.columns.data_type` renders them.
* `style.iconURL` on the service is honoured only by OM versions whose UI reads it; on others the
  default CustomDatabase icon is shown.

## Development

```bash
pip install -e ".[dev]" && pytest
```


### Local E2E stack

`docker/` contains an untouched copy of the official OM compose plus an override that renames
containers/ports (so it coexists with another OM checkout) and drops the Airflow ingestion
container. TiDB Cloud Lake is a managed service, so the stack only runs OpenMetadata; point
`config.yaml` at your Lake cluster and load `fixture.sql` with [lakesql](https://github.com/tidbcloud/lakesql).

```bash
cd docker
docker compose -p lakeom -f om-official.yml -f override.yml up -d
lakesql --dsn "$LAKESQL_DSN" < fixture.sql
# OM: http://localhost:8585 (admin/admin)
OM_ADMIN_EMAIL=admin@open-metadata.org OM_ADMIN_PASSWORD=admin \
  lake-om -c ../config.yaml init-bot --write-token ../.state/bot_token
export OM_JWT_TOKEN=$(cat ../.state/bot_token)
lake-om -c ../config.yaml all
```
