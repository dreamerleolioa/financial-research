#!/usr/bin/env bash
# The GitHub auth action mints the audience-specific ID token using WIF.
set -euo pipefail
: "${GCP_TEST_SERVICE_URL:?Set the deployed service URL}"
: "${GCP_TEST_ID_TOKEN:?Mint an audience-specific ID token before calling the service}"
[[ "$GCP_TEST_SERVICE_URL" =~ ^https://[a-z0-9.-]+\.run\.app$ ]] || exit 1
curl --fail --silent --show-error --retry 3 --max-time 60 \
  --header "X-Serverless-Authorization: Bearer $GCP_TEST_ID_TOKEN" "$GCP_TEST_SERVICE_URL/health"
