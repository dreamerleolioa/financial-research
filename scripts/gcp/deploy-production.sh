#!/usr/bin/env bash
# Updates the pre-created private production service; never changes frontend/schedules.
set -euo pipefail
umask 077
: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
[[ "$GCP_PROJECT_ID" == financial-research-20261019 ]] || { echo 'Wrong production project' >&2; exit 1; }
image=${1:?Pass the image verified against PostgreSQL 17, pinned by digest}
[[ "$image" =~ ^asia-east1-docker\.pkg\.dev/financial-research-20261019/financial-research/backend@sha256:[a-f0-9]{64}$ ]] || exit 1
identity=$(gcloud auth list --filter=status:ACTIVE --format='value(account)')
[[ "$identity" == financial-prod-deploy@financial-research-20261019.iam.gserviceaccount.com ]] || {
  echo 'Unexpected deployment identity' >&2; exit 1;
}
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
verify_service() {
  gcloud run services describe financial-research-prod --project="$GCP_PROJECT_ID" --region=asia-east1 --format=json > "$scratch/service.json"
  gcloud run services get-iam-policy financial-research-prod --project="$GCP_PROJECT_ID" --region=asia-east1 --format=json > "$scratch/iam.json"
  python3 scripts/gcp/verify_production_service.py "$scratch/service.json" "$scratch/iam.json" "$@"
}
# CI can only update an already provisioned, isolated, private production service.
verify_service > /dev/null
gcloud run deploy financial-research-prod \
  --project="$GCP_PROJECT_ID" --region=asia-east1 --image="$image" \
  --service-account="financial-prod-api@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
  --execution-environment=gen2 \
  --add-cloudsql-instances="$GCP_PROJECT_ID:asia-east1:financial-postgres" \
  --cpu=1 --memory=2Gi --min=0 --max=2 --concurrency=4 --timeout=1800 \
  --env-vars-file=scripts/gcp/production-env.yaml \
  --set-secrets=DATABASE_URL=financial-prod-database-url:1,JWT_SECRET=financial-prod-jwt:1,GOOGLE_CLIENT_ID=financial-prod-google-client-id:1,GOOGLE_CLIENT_SECRET=financial-prod-google-client-secret:1,DAILY_RADAR_INTERNAL_TOKEN=financial-prod-internal-token:1,FINMIND_API_TOKEN=financial-prod-finmind-token:1 \
  --quiet
url=$(verify_service "$image")
[[ -z ${GITHUB_OUTPUT:-} ]] || printf 'service_url=%s\n' "$url" >> "$GITHUB_OUTPUT"
printf 'Private production preparation service: %s\n' "$url"
