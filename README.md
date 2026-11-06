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
* An OpenMetadata bot JWT and an administrator-provisioned scoped role. `init-bot`
  uses the explicit `bot.role_id`; it never creates global authorization policies.
* The Lake user needs `SELECT` on `system.*` and `system_history.lineage_history`.
* Lake session timezone must be UTC (the default); the watermark is compared as a naive
  timestamp literal.

## Usage

The CLI is installed as `lake-om` (alias: `lake-openmetadata-ingestion`).

```bash
cp config.example.yaml config.yaml          # edit; ${ENV_VAR} placeholders are expanded
chmod 600 config.yaml

# once, by an OM admin: attach the explicit pre-provisioned role and write a 0600 JWT file
OM_ADMIN_EMAIL=admin@example.com OM_ADMIN_PASSWORD=... lake-om -c config.yaml init-bot
#   with SSO (no basic-auth login):  OM_ADMIN_TOKEN=<any admin JWT> lake-om init-bot
#   token file: --write-token /path/to/token   (default .state/bot_token, always mode 600)
#   print to stdout instead (it stays in the job log): --print-token
export OM_JWT_TOKEN=$(cat .state/bot_token) # or the --write-token path you chose

lake-om -c config.yaml init-service   # create/update the service (idempotent)
lake-om -c config.yaml metadata       # full sync of catalogs/databases/tables/columns
lake-om -c config.yaml lineage        # incremental; --full ignores the watermark
lake-om -c config.yaml all            # the three above
```

Schedule `metadata` (e.g. hourly) and `lineage` (e.g. every 5–15 min) with cron/Airflow. Run
`lineage --full` once after the first `metadata` sync and after any long outage.

The administrator owns the bot role and its effective scope. Re-running `init-bot`
rotates the token; token lifetime defaults to 7 days and cannot exceed 30 days.

### Upgrade notes

`init-bot` no longer prints the bot JWT to stdout by default: it writes it to a 0600 file
(`--write-token PATH`, default `.state/bot_token`, **relative to the process working directory**).
`--print-token` restores the old stdout output byte for byte (the only addition is a warning on
stderr), and `--write-token` / `--print-token` are mutually exclusive.

- **Runbooks / CI that parsed stdout**, e.g. `JWT=$(lake-om -c config.yaml init-bot | sed -n 's/^OM_JWT_TOKEN=//p')`,
  must either add `--print-token` or read the token file: `export OM_JWT_TOKEN=$(cat .state/bot_token)`.
- **Read-only rootfs / non-writable working directory** (e.g. a K8s initContainer with
  `workingDir: /`): pass an explicit writable path, e.g. `--write-token /shared/secrets/bot_token`,
  and mount that path writable. The default is relative to the working directory, not to the
  `--config` file, so it will not land next to `config.yaml` by accident.
- **The token path must not be a symlink.** The file is written atomically and replaces the path, so a
  symlink is replaced by a regular file instead of being written through; point deployments at the
  real mount target (or at a directory on a shared volume) and use the same UID / `fsGroup` as the
  process that reads it.
- **Windows:** the 0600 mode is best-effort — Windows has no POSIX permission bits, so the file relies
  on the user-profile ACL (`mkstemp` already creates it with the closest equivalent).
- **Message text:** the success line is now `token written to PATH (mode 600, sha256:...); export ...`.
  Scripts that matched the old exact text should match the `token written to` prefix.

## Configuration

See `config.example.yaml` for every option. The ones that matter operationally:

| Key | Meaning |
|---|---|
| `lake.dsn` | Connection string of your Lake cluster, e.g. `lake://user:pass@<host>.tidbcloud.com:443/?warehouse=<wh>`. |
| `service.name` | OM service name; part of every FQN. Changing it means a fresh sync into a new service. |
| `metadata.catalogs` | Catalogs to sync (empty = all from `SHOW CATALOGS`). |
| `metadata.mark_deleted` | Soft-delete OM schemas/tables that disappeared from Lake (default `false`; enable explicitly after validating a full sync). |
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

`docker/` contains a hardened local-demo copy of the official OM compose plus an override that renames
containers/ports (so it coexists with another OM checkout) and drops the Airflow ingestion
container. TiDB Cloud Lake is a managed service, so the stack only runs OpenMetadata; point
`config.yaml` at your Lake cluster and load `fixture.sql` with [lakesql](https://github.com/tidbcloud/lakesql).

```bash
cd docker
python3 bootstrap-env.py
docker compose -p lakeom -f om-official.yml -f override.yml up -d
lakesql --dsn "$LAKESQL_DSN" < fixture.sql
# OM: http://localhost:8585; change the built-in admin password before use.
# Local config must explicitly set openmetadata.require_tls: false.
# Set bot.role_id to a role provisioned/reviewed by the OM administrator.
OM_ADMIN_EMAIL=admin@open-metadata.org OM_ADMIN_PASSWORD=admin \
  lake-om -c ../config.yaml init-bot --write-token ../.state/bot_token
export OM_JWT_TOKEN=$(cat ../.state/bot_token)
lake-om -c ../config.yaml all
```
# Transport and verbose diagnostics

For an HTTPS OpenMetadata endpoint signed by a private CA, set
`openmetadata.tls_ca_bundle` to the PEM CA bundle path. Set
`openmetadata.require_tls: true` to reject plaintext endpoints before sending
the bot JWT or admin login. Both settings apply to `init-bot` and normal bot
requests. Certificate verification remains enabled, and bot requests continue
to ignore proxy environment variables. Local HTTP requires an explicit `require_tls: false` opt-out.

Verbose Lake logs contain an operation name, statement fingerprint and byte
count, rather than SQL text and tenant metadata. Statements sent to Lake are
unchanged. The fingerprint can correlate repeated statements without copying
their contents into job logs.

## Hardened defaults

Configuration targets must be regular files with mode 0600 or stricter. Secure
symlink-based secret mounts remain supported; permissions are checked on the
opened target.
Missing `${VARIABLE}` values now fail startup. Both Lake and OpenMetadata require
TLS by default; `require_tls: false` is an explicit local-demo opt-out. OpenMetadata
requests refuse redirects, and custom CA bundles retain certificate/hostname
verification. Bot tokens default to 7 days and are limited to 30 days.

`init-bot` no longer creates an instance-wide policy or role. Set `bot.role_id` to
an existing role reviewed/provisioned by your OM administrator. The tool replaces
the bot's explicit roles with that role, clears admin/team grants in its user
request, and rotates the JWT. It never claims that supplying any arbitrary role
makes it service-scoped: the administrator must verify effective permissions
(including inherited/default roles) against the deployed OM version. OM 2.0's
built-in RuleEvaluator has no `isService(name)` condition; do not invent one.
Existing global policies and exposed credentials are not revoked by merging code;
remove unused grants and rotate/revoke old tokens in the customer-owned instance.

For the local Docker demo, run `python3 docker/bootstrap-env.py` once before
Compose. It creates `docker/.env` atomically with mode 0600 and random database/
Fernet secrets, refuses overwrite, and prints no values. Keep this file with the
existing database volume; changing Fernet keys requires OM's key-rotation process.
Published ports bind only to loopback; self-signup is disabled and API password
masking enabled. This is a local-only demo, not a public deployment template.

### Safe reconciliation and local demo keys

Metadata deletion now defaults off. Enable `metadata.mark_deleted` explicitly
only after a full successful sync. A failed table/stage write suppresses the
entire deletion sweep and produces a nonzero metadata command exit status.

Run `python docker/bootstrap-env.py` before starting the local demo. It creates
unique RSA signing keys in `docker/jwt-keys/` and a unique JWT key ID alongside
the private `.env`. A network-disabled one-shot demo container copies the keys
into a private volume owned by the image's `openmetadata` user; migrate and server
mount that volume read-only. Host and volume key files stay mode 0600 (PKCS8/SPKI
DER), without requiring the host UID to match the container UID. Keep the
same keys for an existing database volume. Existing demo installations must
explicitly plan key/token rotation; the bootstrap refuses to overwrite any
existing secrets and no rotation is performed automatically.
