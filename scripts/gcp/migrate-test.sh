#!/usr/bin/env bash
# Explicit operator action after backing up and draining all test writers.
set -euo pipefail
: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
[[ "$GCP_PROJECT_ID" == financial-research-20261019 ]] || exit 1
[[ ${CONFIRM_TEST_WRITERS_DRAINED:-} == yes ]] || { echo 'Drain test writers and confirm explicitly' >&2; exit 1; }
[[ ${BACKUP_MANIFEST_SHA256:-} =~ ^[a-f0-9]{64}$ ]] || { echo 'Record the verified backup manifest SHA256 before migration' >&2; exit 1; }
image=${1:?Pass the same immutable image digest used by the API}
[[ "$image" =~ ^asia-east1-docker\.pkg\.dev/financial-research-20261019/financial-research/backend@sha256:[a-f0-9]{64}$ ]] || exit 1
gcloud run jobs deploy financial-research-test-migrate \
  --project="$GCP_PROJECT_ID" --region=asia-east1 --image="$image" \
  --service-account="financial-test-migrator@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
  --set-cloudsql-instances="$GCP_PROJECT_ID:asia-east1:financial-postgres" \
  --set-secrets=DATABASE_URL=financial-test-migrator-database-url:1 \
  --set-env-vars=EXPECTED_DATABASE_NAME=financial_research_test \
  --command=sh --args='-c,alembic upgrade head && alembic current --check-heads' \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=1800 --cpu=1 --memory=1Gi --quiet
gcloud run jobs execute financial-research-test-migrate --project="$GCP_PROJECT_ID" --region=asia-east1 --wait --quiet
