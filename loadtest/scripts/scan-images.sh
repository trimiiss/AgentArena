#!/usr/bin/env bash
# Runs the same checks as CI on local images: a Trivy scan and an SBOM per image.
# Both tools run in containers, so nothing needs installing. SBOMs go to sbom/.
#
# Exit status is non-zero if any image has a fixable HIGH or CRITICAL finding.
set -uo pipefail

cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1

TRIVY=aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb
SYFT=anchore/syft:v1.57.0@sha256:3c70e6d5979364cefdc22b51617a78fd264574d3bd6b4f84a351204147025e2a
IMAGES="identity-service agent-registry scenario-service run-orchestrator evaluation-service sample-agent frontend"
SOCKET=/var/run/docker.sock

mkdir -p sbom
failed=0

for name in $IMAGES; do
  image="agentarena/$name:local"
  echo "=== $image"
  docker run --rm -v "$SOCKET:$SOCKET" -v agentarena-trivy-cache:/root/.cache "$TRIVY" image \
    --quiet --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$image" || failed=1
  docker run --rm -v "$SOCKET:$SOCKET" "$SYFT" "docker:$image" -q -o cyclonedx-json > "sbom/$name.cdx.json"
  echo "SBOM: sbom/$name.cdx.json ($(wc -c < "sbom/$name.cdx.json") bytes)"
done

exit "$failed"
