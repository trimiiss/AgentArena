#!/bin/sh
# Creates one database and one role per service. Each role owns only its own
# database and PUBLIC cannot connect, so a service physically cannot read
# another service's data even on this shared local instance.
#
# The PostgreSQL entrypoint runs this file if it is executable and sources it if
# it is not, which depends on how it was checked out. The subshell keeps these
# shell options from leaking into the entrypoint in the second case.
(
  set -eu

  create_database() {
    database=$1
    role=$2
    password=$3
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
      -v role="$role" -v password="$password" <<'SQL'
CREATE ROLE :"role" LOGIN PASSWORD :'password';
SQL
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
      -c "CREATE DATABASE \"$database\" OWNER \"$role\"" \
      -c "REVOKE ALL ON DATABASE \"$database\" FROM PUBLIC"
  }

  create_database identity_db identity_svc "$IDENTITY_DB_PASSWORD"
  create_database agents_db agents_svc "$AGENTS_DB_PASSWORD"
  create_database scenarios_db scenarios_svc "$SCENARIOS_DB_PASSWORD"
  create_database runs_db runs_svc "$RUNS_DB_PASSWORD"
  create_database evaluations_db evaluations_svc "$EVALUATIONS_DB_PASSWORD"
)
