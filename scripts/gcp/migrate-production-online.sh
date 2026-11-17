#!/usr/bin/env bash
# Only called from the approved production release, with a dedicated WIF identity.
set -euo pipefail
: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
[[ "$GCP_PROJECT_ID" == financial-research-20261019 ]] || exit 1
image=${1:?Pass the reviewed image digest}
[[ "$image" =~ ^asia-east1-docker\.pkg\.dev/financial-research-20261019/financial-research/backend@sha256:[a-f0-9]{64}$ ]] || exit 1
identity=$(gcloud auth list --filter=status:ACTIVE --format='value(account)')
[[ "$identity" == "financial-prod-migrate-release@$GCP_PROJECT_ID.iam.gserviceaccount.com" ]] || {
  echo 'The deployment identity cannot start the online migration job.' >&2
  exit 1
}

job=financial-research-prod-online-migrate
gcloud run jobs update "$job" \
  --project="$GCP_PROJECT_ID" --region=asia-east1 --image="$image" \
  --service-account="financial-prod-migrator@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
  --set-cloudsql-instances="$GCP_PROJECT_ID:asia-east1:financial-postgres" \
  --set-secrets=DATABASE_URL=financial-prod-migrator-database-url:1 \
  --set-env-vars=EXPECTED_DATABASE_NAME=financial_research \
  --command=python --args=-m,ai_stock_sentinel.operations.schema,--migrate-online \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=120 --cpu=1 --memory=512Mi --quiet
gcloud run jobs execute "$job" --project="$GCP_PROJECT_ID" --region=asia-east1 --wait --quiet
