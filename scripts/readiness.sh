#!/usr/bin/env bash

# Publish readiness: read-only checks of every layer a release upload depends on. Each check names its stage, the
# request path and status, and the public identity involved; credentials and response bodies are never logged.
# Inputs: STORE_URL, STORE_TOKEN, PUBLIC_KEY, PACKAGE_ID, PACKAGE_VERSION, IMAGE and optional VARIANTS (default source).
# READINESS_VERIFIED_VERSION names the label of an IMAGE the caller already pulled and checked, skipping that check.

readiness_init() {
  READINESS_FAILURES=0
  READINESS_ROWS=()
  READINESS_SCRATCH=$(mktemp -d "${RUNNER_TEMP:-/tmp}/readiness.XXXXXX")
  chmod 700 "$READINESS_SCRATCH"
}

# Records one result; failures print as errors and make the combined result not ready.
readiness_record() {
  local stage=$1 outcome=$2 message=$3
  READINESS_ROWS+=("| $stage | $outcome | ${message//|/\\|} |")
  case "$outcome" in
    fail)
      READINESS_FAILURES=$((READINESS_FAILURES + 1))
      printf '::error::Readiness %s: %s\n' "$stage" "$message" >&2 ;;
    warn) printf '::warning::Readiness %s: %s\n' "$stage" "$message" >&2 ;;
    *) printf 'Readiness %s: %s\n' "$stage" "$message" ;;
  esac
}

# GET a store path; sets HTTP_STATUS (000 on transport failure) and HTTP_BODY, a private file never printed.
readiness_get() {
  local path=$1 authenticated=${2:-}
  HTTP_BODY="$READINESS_SCRATCH/body"
  : > "$HTTP_BODY"
  local config=""
  [[ -z "$authenticated" ]] || config=$(printf 'header = "Authorization: Bearer %s"' "$STORE_TOKEN")
  # -q ignores curlrc; the header travels through stdin, and redirects are refused to keep credentials on origin.
  HTTP_STATUS=$(printf '%s\n' "$config" | curl -q --config - --silent --proto '=http,https' \
    --connect-timeout 10 --max-time 30 --output "$HTTP_BODY" --write-out '%{http_code}' \
    --url "${STORE_URL%/}$path" 2>/dev/null) || HTTP_STATUS=000
  [[ "$HTTP_STATUS" =~ ^[0-9]{3}$ ]] || HTTP_STATUS=000
}

# Describes a non-success response without echoing it.
readiness_http_problem() {
  local path=$1
  case "$HTTP_STATUS" in
    000) printf 'GET %s failed before a complete HTTP response (network, TLS or timeout).' "$path" ;;
    401) printf 'GET %s returned HTTP 401: the store rejected the store token (unknown, revoked, expired, or its owner or namespace grant is disabled).' "$path" ;;
    403) printf 'GET %s returned HTTP 403: the store token lacks the scope this read requires.' "$path" ;;
    3??) printf 'GET %s returned HTTP %s; redirects are not followed, so check store-url.' "$path" "$HTTP_STATUS" ;;
    429|5??) printf 'GET %s returned HTTP %s (rate limit or store availability); this says nothing about the credential.' "$path" "$HTTP_STATUS" ;;
    *) printf 'GET %s returned unexpected HTTP %s; check store-url and the store deployment.' "$path" "$HTTP_STATUS" ;;
  esac
}

# The signing identity's SHA256 fingerprint, as the store and ssh-keygen print it.
readiness_fingerprint() {
  local key="$READINESS_SCRATCH/public.pub"
  printf '%s\n' "${PUBLIC_KEY-}" > "$key"
  [[ "$(awk '{ print $1; exit }' "$key")" == ssh-ed25519 ]] || return 1
  ssh-keygen -l -E sha256 -f "$key" 2>/dev/null | awk '{ print $2 }' | grep -E '^SHA256:[A-Za-z0-9+/]{43}$'
}

# The pinned image: its tag, when present, must still resolve to the pinned digest, and the digest's version label
# must match the tag. Sets READY_IMAGE (name@sha256 form, as release.yml records it) and READY_VERSION.
readiness_check_image() {
  local stage=image tag digest repository resolved
  if [[ ! "$IMAGE" =~ ^(ghcr\.io/mass4org/turian-cli)(:([A-Za-z0-9._-]+))?@(sha256:[0-9a-f]{64})$ ]]; then
    readiness_record "$stage" fail "$IMAGE is not ghcr.io/mass4org/turian-cli pinned by sha256 digest."
    return
  fi
  repository=${BASH_REMATCH[1]} tag=${BASH_REMATCH[3]} digest=${BASH_REMATCH[4]}
  READY_IMAGE="$repository@$digest"
  if [[ -n "$tag" ]]; then
    if ! docker pull -q "$repository:$tag" > /dev/null 2>&1; then
      readiness_record "$stage" fail "Tag $repository:$tag could not be pulled; the pin cannot be confirmed."
      return
    fi
    resolved=$(docker image inspect --format '{{ range .RepoDigests }}{{ println . }}{{ end }}' "$repository:$tag" |
      grep -m1 "^$repository@sha256:" || true)
    if [[ "$resolved" != "$READY_IMAGE" ]]; then
      readiness_record "$stage" fail "Tag $tag resolves to ${resolved#*@} but the workflow pins $digest. A turian-cli tag was re-pushed or the pin is stale; do not release until they agree."
      return
    fi
  fi
  if ! docker pull -q "$READY_IMAGE" > /dev/null 2>&1; then
    readiness_record "$stage" fail "The registry does not serve $READY_IMAGE."
    return
  fi
  READY_VERSION=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$READY_IMAGE")
  if [[ -z "$READY_VERSION" || ( -n "$tag" && "$READY_VERSION" != "$tag" ) ]]; then
    readiness_record "$stage" fail "$READY_IMAGE carries version label '${READY_VERSION:-none}', expected '${tag:-a version}'."
    return
  fi
  readiness_record "$stage" pass "$READY_IMAGE is served and labelled $READY_VERSION${tag:+; tag $tag resolves to the pinned digest}."
}

# The token authenticates and its namespace grants cover the package.
readiness_check_account() {
  local stage=account prefixes
  readiness_get /api/v1/account authenticated
  if [[ "$HTTP_STATUS" != 200 ]]; then
    readiness_record "$stage" fail "$(readiness_http_problem /api/v1/account)"
    return
  fi
  if ! prefixes=$(jq -er '.prefixes | if type == "array" and all(type == "string") then join(",") else error end' \
    "$HTTP_BODY" 2>/dev/null); then
    readiness_record "$stage" fail "GET /api/v1/account returned HTTP 200 with a malformed account document."
    return
  fi
  if ! jq -e --arg id "$PACKAGE_ID" 'any(.prefixes[]; . as $p | $id == $p or ($id | startswith($p + ".")))' \
    "$HTTP_BODY" > /dev/null; then
    readiness_record "$stage" fail "The store token authenticates but is scoped to [${prefixes:-no namespace}], which does not cover $PACKAGE_ID. Issue a token for its namespace: registry.publish for releases, registry.read for scheduled readiness."
    return
  fi
  readiness_record "$stage" pass "GET /api/v1/account returned HTTP 200; the token's namespaces [$prefixes] cover $PACKAGE_ID."
}

# Layer 1: the store's registry signature trust must hold the signing identity, unrevoked and in scope.
readiness_check_registry_trust() {
  local stage=registry-trust decision
  readiness_get /v1/keys
  if [[ "$HTTP_STATUS" != 200 ]]; then
    readiness_record "$stage" fail "$(readiness_http_problem /v1/keys)"
    return
  fi
  if ! decision=$(jq -er --arg signer "$READY_FINGERPRINT" --arg id "$PACKAGE_ID" '
    if (.keys | type) != "array" or ((.revokedKeys // []) | type) != "array" then error("invalid")
    elif any(.revokedKeys[]?; .id == $signer) then "revoked"
    else ([.keys[] | select(.id == $signer)] | first) as $key |
      if $key == null then "missing"
      elif (($key.scopes // []) | type) != "array" then error("invalid")
      elif (($key.scopes // []) | length) == 0 or
        any($key.scopes[]; . as $p | $id == $p or ($id | startswith($p + "."))) then "trusted"
      else "scope" end
    end' "$HTTP_BODY" 2>/dev/null); then
    readiness_record "$stage" fail "GET /v1/keys returned HTTP 200 with an invalid trust document."
    return
  fi
  case "$decision" in
    trusted) readiness_record "$stage" pass "$READY_FINGERPRINT is in registry signature trust (/v1/keys) for $PACKAGE_ID." ;;
    missing) readiness_record "$stage" fail "$READY_FINGERPRINT is absent from /v1/keys. Namespace publisher registration does not add registry trust: a store administrator must add this public key to Registry:TrustedPublicKeys and run the additive --initialize." ;;
    revoked) readiness_record "$stage" fail "$READY_FINGERPRINT is revoked in /v1/keys; the store refuses every signature it makes." ;;
    scope) readiness_record "$stage" fail "$READY_FINGERPRINT is in /v1/keys but its scopes exclude $PACKAGE_ID; a store administrator must widen its registry trust scopes." ;;
  esac
}

# Layer 2: the most specific active namespace claim must list the identity as a publisher key.
readiness_check_namespace() {
  local stage=namespace-publisher claim
  readiness_get /v1/index.json
  if [[ "$HTTP_STATUS" != 200 ]]; then
    readiness_record "$stage" fail "$(readiness_http_problem /v1/index.json)"
    return
  fi
  if ! claim=$(jq -er --arg id "$PACKAGE_ID" '
    (.claims // {}) | if type != "object" then error("invalid") else . end |
    [to_entries[] | select(.key as $p | $id == $p or ($id | startswith($p + ".")))] |
    sort_by(.key | length) | last // {key: "", value: {}} |
    [.key, ((.value.keys // [.value.key // empty]) | join(","))] | @tsv' "$HTTP_BODY" 2>/dev/null); then
    readiness_record "$stage" fail "GET /v1/index.json returned HTTP 200 with invalid namespace claims."
    return
  fi
  local prefix keys
  IFS=$'\t' read -r prefix keys <<< "$claim"
  if [[ -z "$prefix" ]]; then
    readiness_record "$stage" fail "No active namespace claim in /v1/index.json covers $PACKAGE_ID (disabled and keyless claims are not advertised). Register $READY_FINGERPRINT at /account/namespaces."
  elif [[ ",$keys," != *",$READY_FINGERPRINT,"* ]]; then
    readiness_record "$stage" fail "$READY_FINGERPRINT is not a publisher key of namespace $prefix in /v1/index.json. Registry trust does not grant namespace publisher authority: add the key to $prefix at /account/namespaces."
  else
    readiness_record "$stage" pass "$READY_FINGERPRINT is a publisher key of namespace $prefix."
  fi
}

# The token can read the store's own record of each variant; a 404 means not yet published.
readiness_check_provenance() {
  local stage=provenance variant path recorded
  for variant in ${VARIANTS:-source}; do
    path="/api/v1/releases/$PACKAGE_ID/$PACKAGE_VERSION?variant=$variant"
    readiness_get "$path" authenticated
    case "$HTTP_STATUS" in
      404)
        readiness_record "$stage" pass "GET $path returned HTTP 404: $variant $PACKAGE_VERSION is not recorded yet, so this is a first publication." ;;
      200)
        if ! recorded=$(jq -er 'select((.integrity | type) == "string" and (.status | type) == "string") |
          [.status, (.build.image // "")] | @tsv' "$HTTP_BODY" 2>/dev/null); then
          readiness_record "$stage" fail "GET $path returned HTTP 200 with a malformed release receipt."
          continue
        fi
        local status image
        IFS=$'\t' read -r status image <<< "$recorded"
        if [[ -n "$image" && "$image" != "$READY_IMAGE" ]]; then
          readiness_record "$stage" fail "GET $path: $variant $PACKAGE_VERSION is recorded ($status) as built by $image; the store refuses a re-run with $READY_IMAGE. Release a new version instead."
        else
          readiness_record "$stage" pass "GET $path returned HTTP 200: $variant $PACKAGE_VERSION is recorded ($status); an identical re-run is accepted."
        fi ;;
      *) readiness_record "$stage" fail "$(readiness_http_problem "$path")" ;;
    esac
  done
}

# Curator advisories that would refuse or flag this build.
readiness_check_advisories() {
  local stage=advisories rows id reject image range
  readiness_get /api/v1/build-advisories authenticated
  if [[ "$HTTP_STATUS" != 200 ]]; then
    readiness_record "$stage" fail "$(readiness_http_problem /api/v1/build-advisories)"
    return
  fi
  if ! rows=$(jq -er --arg id "$PACKAGE_ID" '
    if type != "array" then error("invalid") else . end |
    map(select((.packagePrefix | type) == "string" and (.id | type) == "string") |
      select(.packagePrefix as $p | $id == $p or ($id | startswith($p + ".")))) |
    map([.id, (.reject == true | tostring), (.image // ""), (.turianVersionRange // "")] |
      map(tostring | gsub("[\u0000-\u001f]"; " ")) | join("\u001f")) | join("\n")' \
    "$HTTP_BODY" 2>/dev/null); then
    readiness_record "$stage" fail "GET /api/v1/build-advisories returned HTTP 200 with an invalid advisory list."
    return
  fi
  local blocking=0
  # A unit separator, unlike a tab, keeps empty selectors in place.
  while IFS=$'\x1f' read -r id reject image range; do
    [[ -n "$id" ]] || continue
    # An advisory matches only when every selector it carries matches, as the store evaluates it.
    [[ -z "$image" || "$image" == "$READY_IMAGE" ]] || continue
    local link="/api/v1/build-advisories/$id"
    if [[ -n "$range" ]]; then
      if [[ "$reject" == true ]]; then
        blocking=1
        readiness_record "$stage" fail "Advisory $id rejects Turian $range for $PACKAGE_ID; readiness does not evaluate ranges, so confirm $READY_VERSION is outside it ($link)."
      else
        readiness_record "$stage" warn "Advisory $id warns for Turian $range builds of $PACKAGE_ID ($link)."
      fi
    elif [[ "$reject" == true ]]; then
      blocking=1
      readiness_record "$stage" fail "Advisory $id rejects builds by $READY_IMAGE for $PACKAGE_ID ($link)."
    else
      readiness_record "$stage" warn "Advisory $id warns for builds by $READY_IMAGE of $PACKAGE_ID ($link)."
    fi
  done <<< "$rows"
  [[ "$blocking" == 1 ]] ||
    readiness_record "$stage" pass "GET /api/v1/build-advisories returned HTTP 200; no advisory rejects $READY_IMAGE for $PACKAGE_ID."
}

# Runs every check, even after a failure, and reports one combined result.
readiness_run() {
  readiness_init
  READY_IMAGE="" READY_VERSION="" READY_FINGERPRINT=""
  if [[ ! "${PACKAGE_ID-}" =~ ^[a-z0-9]+([.-][a-z0-9]+)+$ || ! "${PACKAGE_VERSION-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.+-]+)?$ ]]; then
    readiness_record package fail "Package id '${PACKAGE_ID-}' or version '${PACKAGE_VERSION-}' is not a valid brick identity."
  fi
  if [[ -n "${READINESS_VERIFIED_VERSION-}" ]]; then
    READY_IMAGE=$IMAGE READY_VERSION=$READINESS_VERIFIED_VERSION
  else
    readiness_check_image
  fi
  if READY_FINGERPRINT=$(readiness_fingerprint); then
    readiness_check_registry_trust
    readiness_check_namespace
  else
    readiness_record signing-identity fail "The public-key input is not a single OpenSSH ssh-ed25519 public key."
  fi
  # Its stdout carries the mask for the extracted secret, so it must reach the runner.
  if prepare_store_token 2> /dev/null; then
    readiness_check_account
    readiness_check_provenance
    readiness_check_advisories
  else
    readiness_record token fail "The store-token input is neither a raw mass4_ token nor a JSON export with a string secret; no credential was sent."
  fi
  readiness_report
}

readiness_report() {
  local timestamp result=ready
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  [[ "$READINESS_FAILURES" == 0 ]] || result="not ready"
  printf 'Publish readiness for %s %s at %s: %s (%s failing checks).\n' "${PACKAGE_ID-}" "${PACKAGE_VERSION-}" \
    "${STORE_URL-}" "$result" "$READINESS_FAILURES"
  if [[ -n "${GITHUB_STEP_SUMMARY-}" ]]; then
    {
      echo "### Publish readiness: $result"
      echo
      echo "\`${PACKAGE_ID-}\` ${PACKAGE_VERSION-} at ${STORE_URL-}, signer \`${READY_FINGERPRINT:-unknown}\`, image \`${READY_IMAGE:-unknown}\`, checked $timestamp."
      echo
      echo "| Check | Result | Detail |"
      echo "|---|---|---|"
      printf '%s\n' "${READINESS_ROWS[@]}"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  if [[ -n "${GITHUB_OUTPUT-}" ]]; then
    { echo "ready=$([[ "$result" == ready ]] && echo true || echo false)"; echo "checked-at=$timestamp"; } >> "$GITHUB_OUTPUT"
  fi
  rm -rf "$READINESS_SCRATCH"
  [[ "$result" == ready ]]
}
