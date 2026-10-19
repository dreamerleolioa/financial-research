#!/usr/bin/env bash
# Separate owner operation. CI's API deploy identity cannot act as this migrator.
set -euo pipefail
: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
[[ "$GCP_PROJECT_ID" == financial-research-20261019 ]] || exit 1
[[ ${CONFIRM_PRODUCTION_WRITERS_DRAINED:-} == yes ]] || { echo 'Drain production writers and explicitly confirm' >&2; exit 1; }
[[ ${BACKUP_MANIFEST_SHA256:-} =~ ^[a-f0-9]{64}$ ]] || { echo 'Record the verified restoreable backup SHA256' >&2; exit 1; }
image=${1:?Pass the reviewed API image digest}
[[ "$image" =~ ^asia-east1-docker\.pkg\.dev/financial-research-20261019/financial-research/backend@sha256:[a-f0-9]{64}$ ]] || exit 1
gcloud run jobs deploy financial-research-prod-migrate \
  --project="$GCP_PROJECT_ID" --region=asia-east1 --image="$image" \
  --service-account="financial-prod-migrator@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
  --set-cloudsql-instances="$GCP_PROJECT_ID:asia-east1:financial-postgres" \
  --set-secrets=DATABASE_URL=financial-prod-migrator-database-url:1 \
  --set-env-vars=EXPECTED_DATABASE_NAME=financial_research \
  --command=sh --args='-c,alembic upgrade head && alembic current --check-heads' \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=1800 --cpu=1 --memory=1Gi --quiet
gcloud run jobs execute financial-research-prod-migrate --project="$GCP_PROJECT_ID" --region=asia-east1 --wait --quiet
