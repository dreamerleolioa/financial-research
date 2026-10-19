#!/usr/bin/env bash
# Run in the Zeabur PostgreSQL 17 container using its local Unix socket.
# Also supports read-only target manifests. No passwords are printed or embedded.
set -euo pipefail
umask 077
mode=${1:?export-financial|export-health-schema|verify-financial|verify-health-schema}
out=${2:?Pass a new output directory outside the repository}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
case "$mode" in
  export-financial|verify-financial) list="$script_dir/financial-tables.txt"; schema_only=false ;;
  export-health-schema|verify-health-schema) list="$script_dir/health-tables.txt"; schema_only=true ;;
  *) echo 'Invalid snapshot mode' >&2; exit 1 ;;
esac
: "${PGDATABASE:?Set PGDATABASE to the selected database (not a URL)}"
[[ "$PGDATABASE" =~ ^[a-z][a-z0-9_-]*$ ]] || { echo 'PGDATABASE must be a database name; use PG environment variables for connection settings' >&2; exit 1; }
[[ $(psql -XqAt -w -v ON_ERROR_STOP=1 -c 'SHOW server_version_num') == 17???? ]] || {
  echo 'This rehearsal requires PostgreSQL 17' >&2; exit 1;
}
[[ $(pg_dump --version) =~ PostgreSQL\)\ 17\. ]] || { echo 'Use pg_dump 17 from the source container' >&2; exit 1; }
mkdir -m 700 -- "$out"
out=$(cd -- "$out" && pwd)
cp -- "$list" "$out/tables.txt"
args=()
relations=()
while IFS= read -r relation; do
  [[ "$relation" =~ ^(public|maintenance_backup)\.[a-z0-9_]+$ ]] || exit 1
  args+=(--table="$relation")
  relations+=("'$relation'::regclass")
done < "$list"
relation_sql=$(IFS=,; printf '%s' "${relations[*]}")

# Financial exports are table-selective. Health exports carry the complete
# isolated database schema, including enum, extension, function and trigger deps.
psql -XqAt -w -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF $schema_only AND (EXISTS (SELECT FROM pg_namespace
      WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg\_%')
      OR EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND c.oid NOT IN ($relation_sql))) THEN
    RAISE EXCEPTION 'Unexpected health schema objects; review before full schema export';
  END IF;
  IF EXISTS (SELECT FROM pg_class WHERE oid IN ($relation_sql)
      AND (relkind <> 'r' OR relrowsecurity OR relforcerowsecurity)) THEN
    RAISE EXCEPTION 'Partitioned tables or RLS require dependency and privilege review';
  END IF;
  IF NOT $schema_only AND EXISTS (SELECT FROM pg_trigger WHERE tgrelid IN ($relation_sql) AND NOT tgisinternal) THEN
    RAISE EXCEPTION 'User triggers require dependency review before export';
  END IF;
  IF NOT $schema_only AND EXISTS (SELECT FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid
      JOIN pg_namespace n ON n.oid=t.typnamespace
      WHERE a.attrelid IN ($relation_sql) AND a.attnum>0 AND NOT a.attisdropped AND n.nspname <> 'pg_catalog') THEN
    RAISE EXCEPTION 'User column types require dependency review before export';
  END IF;
  IF EXISTS (SELECT FROM pg_constraint WHERE conrelid IN ($relation_sql)
      AND contype='f' AND confrelid NOT IN ($relation_sql)) THEN
    RAISE EXCEPTION 'Cross-application foreign key detected';
  END IF;
  IF NOT $schema_only AND EXISTS (SELECT FROM pg_depend dep JOIN pg_proc p ON p.oid=dep.refobjid
      JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE dep.refclassid='pg_proc'::regclass AND n.nspname <> 'pg_catalog'
      AND ((dep.classid='pg_attrdef'::regclass AND dep.objid IN
        (SELECT oid FROM pg_attrdef WHERE adrelid IN ($relation_sql)))
        OR (dep.classid='pg_class'::regclass AND dep.objid IN
        (SELECT indexrelid FROM pg_index WHERE indrelid IN ($relation_sql)))
        OR (dep.classid='pg_constraint'::regclass AND dep.objid IN
        (SELECT oid FROM pg_constraint WHERE conrelid IN ($relation_sql))))) THEN
    RAISE EXCEPTION 'User functions require dependency review before export';
  END IF;
END \$\$;
SQL

# Hold one exported MVCC snapshot until both dump and row manifests finish.
mkfifo "$out/snapshot-input" "$out/snapshot-output"
psql -XqAt -w -v ON_ERROR_STOP=1 < "$out/snapshot-input" > "$out/snapshot-output" &
snapshot_pid=$!
exec 3> "$out/snapshot-input"
exec 4< "$out/snapshot-output"
cleanup() {
  printf 'ROLLBACK;\n\\q\n' >&3 2>/dev/null || true
  exec 3>&- 4<&-
  wait "$snapshot_pid" 2>/dev/null || true
  rm -f -- "$out/snapshot-input" "$out/snapshot-output"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n' >&3
IFS= read -r snapshot <&4
[[ "$snapshot" =~ ^[A-Fa-f0-9]+-[A-Fa-f0-9]+-[0-9]+$ ]] || { echo 'Snapshot creation failed' >&2; exit 1; }
date -u +%FT%TZ > "$out/started-at.txt"

if [[ "$mode" == export-* ]]; then
  # Explicitly include owned sequences. Sequence state itself is not MVCC;
  # the actual setval values in the dump are the restore source of truth.
  # Keep the query's exit status: process substitution would hide a psql error.
  psql -XqAt -w -v ON_ERROR_STOP=1 -c "SELECT quote_ident(n.nspname)||'.'||quote_ident(s.relname)
    FROM pg_class s JOIN pg_namespace n ON n.oid=s.relnamespace JOIN pg_depend d ON d.objid=s.oid
    WHERE s.relkind='S' AND d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass
      AND d.refobjid IN ($relation_sql) AND d.deptype IN ('a','i') ORDER BY 1" > "$out/sequences.txt"
  while IFS= read -r sequence; do
    [[ "$sequence" =~ ^(public|maintenance_backup)\.[a-z0-9_]+$ ]] || exit 1
    args+=(--table="$sequence")
  done < "$out/sequences.txt"
  # pg_dump -t omits enum/function/extension definitions needed by health.
  [[ "$schema_only" == false ]] || args=(--schema-only)
  {
    [[ "$schema_only" == true ]] || printf 'CREATE SCHEMA maintenance_backup;\n'
    pg_dump -w --no-owner --no-acl --no-security-labels --strict-names --snapshot="$snapshot" "${args[@]}"
  } | gzip -c > "$out/database.sql.gz"
fi

while IFS= read -r relation; do
  {
    printf "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSET TRANSACTION SNAPSHOT '%s';\n" "$snapshot"
    cat "$script_dir/table-manifest.sql"
    printf '\nCOMMIT;\n'
  } | psql -XqAt -w -v ON_ERROR_STOP=1 -v relation="$relation" -v full_schema="$schema_only" -v schema_only="$([[ "$mode" == export-health-schema ]] && echo true || echo false)" >> "$out/manifest.jsonl"
done < "$list"
if [[ "$mode" == export-* ]]; then
  gzip -cd "$out/database.sql.gz" | sed -n '/^SELECT pg_catalog.setval(/p' > "$out/sequence-state.sql"
  if command -v sha256sum >/dev/null; then
    (cd -- "$out" && sha256sum database.sql.gz manifest.jsonl tables.txt sequence-state.sql > SHA256SUMS)
  else
    (cd -- "$out" && shasum -a 256 database.sql.gz manifest.jsonl tables.txt sequence-state.sql > SHA256SUMS)
  fi
fi
date -u +%FT%TZ > "$out/finished-at.txt"
printf 'Snapshot completed: %s\n' "$out"
