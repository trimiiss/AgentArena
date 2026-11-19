#!/usr/bin/env bash
# Proves one complete path through a deployed system, from sign-up to a benchmark.
# Exits non-zero on any failure, so a pipeline can stop on it.
#
#   scripts/smoke.sh                               Compose gateway
#   BASE_URL=http://127.0.0.1:8088 scripts/smoke.sh   kind ingress
set -euo pipefail

BASE_URL=${BASE_URL:-http://127.0.0.1:8080}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-120}

fail() { echo "SMOKE FAILED: $1" >&2; exit 1; }

# Reads one JSON field with sed, so the script needs nothing beyond curl.
field() { sed -n "s/.*\"$1\":\"\{0,1\}\([^\",}]*\).*/\1/p" | head -n 1; }

request() {
  local method=$1 path=$2 body=${3:-}
  local arguments=(-sS -m 15 -X "$method" -H 'Content-Type: application/json')
  [ -n "${TOKEN:-}" ] && arguments+=(-H "Authorization: Bearer $TOKEN")
  [ -n "$body" ] && arguments+=(-d "$body")
  curl "${arguments[@]}" -w '\n%{http_code}' "$BASE_URL$path"
}

# Prints the body, or stops the script unless the status is the expected one.
expect() {
  local expected=$1 method=$2 path=$3 body=${4:-}
  local response status
  response=$(request "$method" "$path" "$body")
  status=${response##*$'\n'}
  [ "$status" = "$expected" ] || fail "$method $path returned $status, wanted $expected: ${response%$'\n'*}"
  printf '%s' "${response%$'\n'*}"
}

echo "Smoke test against $BASE_URL"

curl -fsS -m 10 -o /dev/null "$BASE_URL/" || fail "the web app is not reachable"
echo "  web app responds"

expect 401 GET /v1/agents >/dev/null
echo "  API rejects anonymous requests"

suffix="$(date +%s)-$RANDOM"
session=$(expect 201 POST /v1/auth/register \
  "{\"email\":\"smoke-$suffix@example.com\",\"password\":\"smoke-test-password\",\"display_name\":\"Smoke\",\"organization_name\":\"Smoke $suffix\"}")
TOKEN=$(printf '%s' "$session" | field access_token)
[ -n "$TOKEN" ] || fail "registration returned no token"
echo "  registered a user"

pack=$(expect 200 POST /v1/scenarios/starter-pack)
suite_id=$(printf '%s' "$pack" | field suite_id)
echo "  installed the starter pack"

agent=$(expect 201 POST /v1/agents \
  '{"name":"Smoke agent","agent_type":"customer_support","endpoint_url":"http://sample-agent:8000/agents/v2"}')
agent_id=$(printf '%s' "$agent" | field id)
echo "  registered an agent"

validation=$(expect 200 POST "/v1/agents/$agent_id/validate")
case "$validation" in *'"valid":true'*) ;; *) fail "agent validation failed: $validation" ;; esac
echo "  agent answered the protocol handshake"

run=$(expect 202 POST /v1/runs "{\"agent_id\":\"$agent_id\",\"suite_id\":\"$suite_id\",\"concurrency\":6}")
run_id=$(printf '%s' "$run" | field id)
echo "  started run $run_id"

deadline=$((SECONDS + TIMEOUT_SECONDS))
status=""
while [ "$SECONDS" -lt "$deadline" ]; do
  status=$(expect 200 GET "/v1/runs/$run_id" | sed -n 's/.*"status":"\([a-z]*\)","agent_id.*/\1/p')
  case "$status" in completed) break ;; failed | cancelled) fail "run ended as $status" ;; esac
  sleep 1
done
[ "$status" = completed ] || fail "run did not finish within $TIMEOUT_SECONDS s (last status: ${status:-unknown})"
echo "  run completed"

deadline=$((SECONDS + 30))
while [ "$SECONDS" -lt "$deadline" ]; do
  benchmark=$(request GET "/v1/benchmarks/runs/$run_id")
  case "$benchmark" in *'"final":true'*) break ;; esac
  sleep 1
done
case "$benchmark" in
  *'"verdict":"passed"'*) echo "  benchmark passed" ;;
  *) fail "expected a passing benchmark: ${benchmark:0:300}" ;;
esac

echo "Smoke test passed"
