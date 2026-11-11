#!/usr/bin/env bash
# Separate owner operation. CI's API deploy identity cannot act as this migrator.
set -euo pipefail
: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
[[ "$GCP_PROJECT_ID" == financial-research-20261019 ]] || exit 1
[[ ${CONFIRM_PRODUCTION_WRITERS_DRAINED:-} == yes ]] || { echo 'Drain production writers and explicitly confirm' >&2; exit 1; }
[[ ${CONFIRM_PRODUCTION_MIGRATION:-} == yes ]] || { echo 'Explicit production migration approval required' >&2; exit 1; }
[[ ${CONFIRM_BACKUP_RESTORE_VERIFIED:-} == yes ]] || { echo 'Restore the backup and verify sequences before confirming' >&2; exit 1; }
[[ ${BACKUP_MANIFEST_SHA256:-} =~ ^[a-f0-9]{64}$ ]] || { echo 'Record the verified restoreable backup SHA256' >&2; exit 1; }
: "${BACKUP_BUNDLE_SHA256:?Pass the trusted SHA256SUMS checksum}"
: "${BACKUP_DIRECTORY:?Pass the actual backup directory}"
: "${BACKUP_RESTORED_MANIFEST:?Pass the isolated restore table manifest}"
: "${PRODUCTION_MIGRATION_FROM_REVISION:?Pass the approved starting revision}"
: "${PRODUCTION_MIGRATION_TO_REVISION:?Pass the approved image head}"
[[ "$PRODUCTION_MIGRATION_FROM_REVISION" =~ ^[a-f0-9]{12}$ && "$PRODUCTION_MIGRATION_TO_REVISION" =~ ^[a-f0-9]{12}$ ]] || exit 1
image=${1:?Pass the reviewed API image digest}
[[ "$image" =~ ^asia-east1-docker\.pkg\.dev/financial-research-20261019/financial-research/backend@sha256:[a-f0-9]{64}$ ]] || exit 1
umask 077
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
python3 scripts/gcp/verify-migration-backup.py --backup "$BACKUP_DIRECTORY" \
  --restored-manifest "$BACKUP_RESTORED_MANIFEST" --manifest-sha256 "$BACKUP_MANIFEST_SHA256" \
  --bundle-sha256 "$BACKUP_BUNDLE_SHA256" --from-revision "$PRODUCTION_MIGRATION_FROM_REVISION" > "$scratch/backup.json"
backup_tables_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup_tables_sha256"])' "$scratch/backup.json")
backup_preimage_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup_preimage_sha256"])' "$scratch/backup.json")
approval_expires=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["approval_expires_at"])' "$scratch/backup.json")
identity=$(gcloud auth list --filter=status:ACTIVE --format='value(account)')
[[ "$identity" == tonyliao1994@gmail.com ]] || { echo 'Use the separately approved maintenance operator, not deployment CI' >&2; exit 1; }
gcloud run services describe financial-research-prod --project="$GCP_PROJECT_ID" --region=asia-east1 --format=json > "$scratch/service.json"
gcloud run services get-iam-policy financial-research-prod --project="$GCP_PROJECT_ID" --region=asia-east1 --format=json > "$scratch/iam.json"
python3 scripts/gcp/verify_production_service.py "$scratch/service.json" "$scratch/iam.json" --access-mode "${GCP_PRODUCTION_ACCESS_MODE:-private}" > /dev/null
gh api 'repos/dreamerleolioa/financial-research/actions/workflows?per_page=100' > "$scratch/workflows.json"
python3 scripts/gcp/verify-production-drain.py "$scratch/service.json" "$scratch/workflows.json"
gcloud run jobs deploy financial-research-prod-migrate \
  --project="$GCP_PROJECT_ID" --region=asia-east1 --image="$image" \
  --service-account="financial-prod-migrator@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
  --set-cloudsql-instances="$GCP_PROJECT_ID:asia-east1:financial-postgres" \
  --set-secrets=DATABASE_URL=financial-prod-migrator-database-url:1 \
  --set-env-vars="EXPECTED_DATABASE_NAME=financial_research,CONFIRM_PRODUCTION_MIGRATION=yes,MIGRATION_BACKUP_TABLES_SHA256=$backup_tables_sha,MIGRATION_BACKUP_PREIMAGE_SHA256=$backup_preimage_sha,MIGRATION_BACKUP_MANIFEST_SHA256=$BACKUP_MANIFEST_SHA256,MIGRATION_APPROVAL_EXPIRES_AT=$approval_expires" \
  --command=python --args="-m,ai_stock_sentinel.operations.schema,--migrate,--from-revision,$PRODUCTION_MIGRATION_FROM_REVISION,--to-revision,$PRODUCTION_MIGRATION_TO_REVISION" \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=1800 --cpu=1 --memory=1Gi --quiet
gcloud run jobs execute financial-research-prod-migrate --project="$GCP_PROJECT_ID" --region=asia-east1 --wait --quiet
