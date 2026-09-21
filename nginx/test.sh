#!/usr/bin/env bash
# Exercises nginx/default.conf for real: builds the image, puts it in front of
# two stub upstreams (named `backend` and `frontend`, as in docker-compose.yml)
# on a throwaway Docker network, and checks the behaviour the config exists
# for. Run from anywhere:  bash nginx/test.sh   (needs Docker and curl).
#
# Not a unit test of nginx — a regression check for what would otherwise only
# show up on the production box: routing, the identity header reaching the
# backend, and (ADR-0002) the media token never reaching a log.
set -euo pipefail

cd "$(dirname "$0")"

id="nginx-test-$$"
img="$id:latest"
net="$id"
tmp="$(mktemp -d)"
failures=0

cleanup() {
  docker rm -f "$id-proxy" "$id-backend" "$id-frontend" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  docker rmi "$img" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

pass() { echo "  ok   $1"; }
fail() { echo "  FAIL $1"; failures=$((failures + 1)); }

# expect_contains <label> <haystack> <needle>
expect_contains() {
  if [[ "$2" == *"$3"* ]]; then pass "$1"; else fail "$1 (expected to find: $3 — got: $2)"; fi
}
# expect_absent <label> <haystack> <needle>
expect_absent() {
  if [[ "$2" != *"$3"* ]]; then pass "$1"; else fail "$1 (must not contain: $3)"; fi
}

docker build -q -t "$img" . >/dev/null
docker network create "$net" >/dev/null

# Stub upstreams: plain nginx answering with what it saw, so the assertions can
# see the exact URI and headers the proxy forwarded.
cat > "$tmp/backend.conf" <<'EOF'
server { listen 8000; location / { return 200 "backend uri=$request_uri user=$http_x_test_user host=$http_host range=$http_range"; } }
EOF
cat > "$tmp/frontend.conf" <<'EOF'
server { listen 4173; location / { return 200 "frontend uri=$request_uri"; } }
EOF

start_stub() { # <name>
  docker run -d --rm --name "$id-$1" --network "$net" --network-alias "$1" \
    -v "$tmp/$1.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.27-alpine >/dev/null
}

# The proxy starts first, with neither upstream running yet. It must come up
# anyway (upstreams are resolved per request, not at startup)...
docker run -d --rm --name "$id-proxy" --network "$net" -p 127.0.0.1::80 "$img" >/dev/null
port="$(docker port "$id-proxy" 80/tcp | head -n1 | sed 's/.*://')"
base="http://127.0.0.1:$port"

for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$base/api/health" || true)"
  [ "$code" = "502" ] && break
  sleep 1
done
[ "$code" = "502" ] && pass "starts and answers 502 while its upstreams do not exist yet" \
  || fail "starts before its upstreams (got HTTP $code, wanted 502)"

# ...and pick them up once they do.
start_stub backend
start_stub frontend
for _ in $(seq 1 40); do
  out="$(curl -s "$base/api/health" || true)"
  [[ "$out" == backend* ]] && break
  sleep 1
done
expect_contains "finds the backend once it appears (no reload needed)" "$out" "backend uri=/api/health"

echo "routing"
expect_contains "/ goes to the frontend" "$(curl -s "$base/")" "frontend uri=/"
expect_contains "a client-side route goes to the frontend" "$(curl -s "$base/analyses/42")" "frontend uri=/analyses/42"
expect_contains "/api/... goes to the backend with the path untouched" \
  "$(curl -s "$base/api/analyses?test_id=T001")" "backend uri=/api/analyses?test_id=T001"

echo "identity header (Mechatronics) and Host"
out="$(curl -s -H 'X-Test-User: jan.peeters@vives.be' -H 'Host: dogtrace.example:5173' "$base/api/whoami")"
expect_contains "the identity header reaches the backend untouched" "$out" "user=jan.peeters@vives.be"
expect_contains "Host keeps the port the client used" "$out" "host=dogtrace.example:5173"
expect_contains "Range requests pass through for video seeking" \
  "$(curl -s -H 'Range: bytes=0-99' "$base/api/media/stream?key=k")" "range=bytes=0-99"

echo "redirects"
location="$(curl -si "$base/api" | tr -d '\r' | awk 'tolower($1)=="location:" {print $2}')"
[ "$location" = "/api/" ] && pass "/api redirects to a relative /api/ (no http://host:port baked in)" \
  || fail "/api redirect Location was '$location', wanted the relative '/api/'"

echo "limits"
code="$(head -c 2097152 /dev/zero | curl -s -o /dev/null -w '%{http_code}' -X POST --data-binary @- "$base/api/analyses")"
[ "$code" = "413" ] && pass "a 2 MB request body is refused with 413" || fail "2 MB body got HTTP $code, wanted 413"

echo "media token never reaches a log (ADR-0002)"
curl -s -o /dev/null "$base/api/media/stream?key=cuts/T001/a.mp4&action=play&token=SECRET-ONE"
curl -s -o /dev/null "$base/api/media/stream?key=k&token=SECRET-TWO&token=SECRET-THREE"
curl -s -o /dev/null "$base/api/media/%73tream?key=k&token=SECRET-FOUR"
curl -s -o /dev/null "$base/api/media/stream?token=SECRET-FIVE&key=k"
curl -s -o /dev/null "$base/api/media/tests"
logs="$(docker logs "$id-proxy" 2>&1)"
for secret in SECRET-ONE SECRET-TWO SECRET-THREE SECRET-FOUR SECRET-FIVE; do
  expect_absent "$secret is not in the proxy logs" "$logs" "$secret"
done
expect_contains "the stream requests are still logged (by path)" "$logs" "GET /api/media/stream "
expect_contains "other requests keep their full URI in the log" "$logs" "GET /api/media/tests "

echo
if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed"
  echo "--- proxy log ---"; docker logs "$id-proxy" 2>&1 | tail -30
  exit 1
fi
echo "all nginx checks passed"
