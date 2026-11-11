#!/usr/bin/env bash
# Update/execute only the pre-created read-only job. No migration privileges.
set -euo pipefail
: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
[[ "$GCP_PROJECT_ID" == financial-research-20261019 ]] || exit 1
image=${1:?Pass the reviewed image digest}
[[ "$image" =~ ^asia-east1-docker\.pkg\.dev/financial-research-20261019/financial-research/backend@sha256:[a-f0-9]{64}$ ]] || exit 1
if ! gcloud run jobs update financial-research-prod-schema-check \
  --project="$GCP_PROJECT_ID" --region=asia-east1 --image="$image" \
  --service-account="financial-prod-schema-check@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
  --set-cloudsql-instances="$GCP_PROJECT_ID:asia-east1:financial-postgres" \
  --set-secrets=PGPASSWORD=financial-prod-debug-password:1 \
  --set-env-vars="PGHOST=/cloudsql/$GCP_PROJECT_ID:asia-east1:financial-postgres,PGDATABASE=financial_research,PGUSER=financial_prod_debug_readonly" \
  --command=python --args=-m,ai_stock_sentinel.operations.schema \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=120 --cpu=1 --memory=512Mi --quiet; then
  echo 'Cannot configure the read-only schema checker. Complete the separately approved job/IAM setup in docs/gcp-schema-migration-runbook.md; no service update was attempted.' >&2
  exit 1
fi
if ! gcloud run jobs execute financial-research-prod-schema-check \
  --project="$GCP_PROJECT_ID" --region=asia-east1 --wait --quiet; then
  echo 'Production schema preflight failed. No service update was attempted. Inspect the job report; if migration_required, use the separately approved migrate-production.sh backup/drain flow, then retry deployment.' >&2
  exit 1
fi
