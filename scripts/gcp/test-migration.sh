#!/usr/bin/env bash
# Disposable PG17 fixture, published only on loopback; no source data or credentials.
set -euo pipefail
clients=$(mktemp -d /tmp/fr-gcp-clients.XXXXXX)
container_id=
cleanup() {
  if [[ -n "$container_id" ]]; then docker rm -f "$container_id" >/dev/null; fi
  rm -rf -- "$clients"
}
trap cleanup EXIT
container_id=$(docker run --detach \
  --env POSTGRES_PASSWORD=fixture-only-password \
  --publish 127.0.0.1:65439:5432 \
  postgres:17-bookworm -c cluster_name=gcp-migration-fixture)
ready=false
for attempt in {1..30}; do
  if docker exec "$container_id" pg_isready -U postgres >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
[[ "$ready" == true ]] || { docker logs "$container_id" >&2; exit 1; }
export GCP_MIGRATION_TEST_CONTAINER="$container_id"
for client in psql pg_dump createdb dropdb; do
  cat > "$clients/$client" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
client=$(basename "$0")
exec docker exec -i \
  --env PGHOST=127.0.0.1 --env PGPORT=5432 \
  --env PGUSER=postgres --env PGPASSWORD=fixture-only-password \
  --env PGDATABASE="${PGDATABASE:-postgres}" \
  "${GCP_MIGRATION_TEST_CONTAINER:?}" "$client" "$@"
WRAPPER
  chmod 700 "$clients/$client"
done
export GCP_MIGRATION_TEST_HOST=127.0.0.1 GCP_MIGRATION_TEST_BIN="$clients"
export PGUSER=postgres PGPASSWORD=fixture-only-password
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../backend"
uv run pytest tests/test_gcp_migration.py -q
