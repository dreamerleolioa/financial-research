#!/usr/bin/env bash
# Uses an already configured gcp-test identity. Never changes the production endpoint.
set -euo pipefail
: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
[[ "$GCP_PROJECT_ID" == financial-research-20261019 ]] || { echo 'Unexpected test project' >&2; exit 1; }
region=asia-east1
image=${1:?Pass an immutable Artifact Registry image digest}
[[ "$image" =~ ^asia-east1-docker\.pkg\.dev/financial-research-20261019/financial-research/backend@sha256:[a-f0-9]{64}$ ]] || {
  echo 'Expected the test project image pinned by digest' >&2; exit 1;
}

gcloud run deploy financial-research-test \
  --project="$GCP_PROJECT_ID" --region="$region" --image="$image" \
  --service-account="financial-test-api@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
  --no-allow-unauthenticated --execution-environment=gen2 \
  --add-cloudsql-instances="$GCP_PROJECT_ID:$region:financial-postgres" \
  --cpu=1 --memory=2Gi --min=0 --max=2 --concurrency=4 --timeout=1800 \
  --env-vars-file=scripts/gcp/test-env.yaml \
  --set-secrets=DATABASE_URL=financial-test-database-url:1,JWT_SECRET=financial-test-jwt:1,GOOGLE_CLIENT_ID=financial-test-google-client-id:1,GOOGLE_CLIENT_SECRET=financial-test-google-client-secret:1,DAILY_RADAR_INTERNAL_TOKEN=financial-test-internal-token:1,FINMIND_API_TOKEN=financial-test-finmind-token:1 \
  --quiet

url=$(gcloud run services describe financial-research-test --project="$GCP_PROJECT_ID" --region="$region" --format='value(status.url)')
[[ "$url" == https://*.run.app ]] || { echo 'Missing Cloud Run URL' >&2; exit 1; }
[[ -z ${GITHUB_OUTPUT:-} ]] || printf 'service_url=%s\n' "$url" >> "$GITHUB_OUTPUT"
printf '\nTest service: %s\n' "$url"
