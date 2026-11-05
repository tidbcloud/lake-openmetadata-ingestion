#!/bin/bash
# Runs after the upstream mysql-script.sql when initializing a new local volume.
# The upstream image creates demo users with public passwords; replace both.
set -eu
case "${MYSQL_PASSWORD:-}:${AIRFLOW_DB_PASSWORD:-}" in
  *[!0-9a-f:]*|:*|*:) echo 'Generate private hex database passwords with bootstrap-env.py' >&2; exit 1;;
esac
MYSQL_PWD="${MYSQL_ROOT_PASSWORD:?}" mysql --protocol=socket --user=root <<SQL
ALTER USER 'openmetadata_user'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';
ALTER USER 'airflow_user'@'%' IDENTIFIED BY '${AIRFLOW_DB_PASSWORD}';
REVOKE GRANT OPTION ON openmetadata_db.* FROM 'openmetadata_user'@'%';
REVOKE PROCESS ON *.* FROM 'openmetadata_user'@'%';
REVOKE GRANT OPTION ON airflow_db.* FROM 'airflow_user'@'%';
SQL
