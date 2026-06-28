#!/usr/bin/env bash
# Publish every .avsc under stube/ to an Apicurio schema registry.
#
# Dry-run by default: it lists what *would* be published and exits 0. Fill in
# the HTTP POST below to actually upload to your registry.
#
# Expected env:
#   APICURIO_URL   registry base URL, e.g. https://apicurio.example:8080
#   APICURIO_AUTH  bearer token or basic-auth header value (optional)
#   GROUP_ID       Apicurio group id; defaults to "zaentrum"

set -euo pipefail

APICURIO_URL="${APICURIO_URL:-https://apicurio.example:8080}"
GROUP_ID="${GROUP_ID:-zaentrum}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Apicurio target : ${APICURIO_URL}"
echo "Group id        : ${GROUP_ID}"
echo "Schema root     : ${ROOT}/stube/"
echo

find "${ROOT}/stube" -name '*.avsc' -print0 | while IFS= read -r -d '' schema; do
  rel="${schema#${ROOT}/}"
  artifact_id="$(echo "${rel}" | sed 's|/|.|g' | sed 's|\.avsc$||')"
  echo "  would publish artifactId=${artifact_id} from ${rel}"
  # TODO: actually POST to ${APICURIO_URL}/apis/registry/v2/groups/${GROUP_ID}/artifacts
  #       with X-Registry-ArtifactId: ${artifact_id} and Content-Type: application/json
done

echo
echo "(dry run — no HTTP calls made)"
