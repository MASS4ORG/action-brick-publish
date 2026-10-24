#!/usr/bin/env bash

# Accept the store's JSON export or its raw secret without exposing either in diagnostics.
prepare_store_token() {
  local prepared token format normalized
  if ! prepared=$(printf '%s' "${STORE_TOKEN-}" | jq -Rrsj '
    def trim: gsub("^[[:space:]\uFEFF]+|[[:space:]\uFEFF]+$"; "");
    . as $raw | trim as $input |
    ($input | startswith("{")) as $json |
    (if $json then
      ($input | fromjson) |
      if type == "object" and (.secret | type) == "string" then .secret
      else error("invalid credential") end
    else $input end) as $secret |
    ($secret | trim) as $token |
    if ($token | length) == 49 and ($token | test("^mass4_[A-Za-z0-9_-]{43}$")) then
      [$token, (if $json then "json" else "raw" end),
       (($raw != $input or $secret != $token) | tostring)] | @tsv
    else error("invalid credential") end
  ' 2>/dev/null); then
    printf '%s\n' '::error::Invalid store-token input. Supply the raw mass4_ token or a JSON object with a string secret field. Surrounding whitespace is trimmed automatically; internal whitespace, missing secret fields and malformed JSON are rejected. No credential was sent.' >&2
    return 1
  fi

  IFS=$'\t' read -r token format normalized <<< "$prepared"
  # Register the extracted value too: GitHub initially masks only the original input.
  printf '::add-mask::%s\n' "$token"
  printf 'Store credential: format=%s; surrounding-whitespace-normalized=%s.\n' "$format" "$normalized"
  STORE_TOKEN=$token

  local host="${STORE_URL#*://}"
  host="${host%%[:/]*}"
  variable="GAYA_REGISTRY_TOKEN_$(tr '[:lower:]' '[:upper:]' <<< "$host" | tr -c 'A-Z0-9\n' '_')"
  export "$variable=$token"
}

# Check authentication independently of release visibility; discard all response content.
check_store_authentication() {
  local status code=0
  # -q ignores curlrc; the header travels through stdin, never the process arguments.
  # Redirects are refused so credentials stay on the configured store origin.
  status=$(printf 'header = "Authorization: Bearer %s"\n' "$STORE_TOKEN" |
    curl -q --config - --silent --show-error --proto '=http,https' \
      --connect-timeout 10 --max-time 30 --output /dev/null --write-out '%{http_code}' \
      --url "${STORE_URL%/}/api/v1/account" 2>/dev/null) || code=$?
  if [[ "$code" != 0 ]]; then
    printf '::error::Store authentication check failed before receiving a complete HTTP response (network, TLS or timeout; curl exit %s). The credential was not logged; check store availability and runner connectivity.\n' "$code" >&2
    return 1
  fi
  case "$status" in
    200)
      printf '%s\n' 'Store authentication: GET /api/v1/account returned HTTP 200. Checking release provenance and publish permission next.' ;;
    401)
      printf '%s\n' '::error::Store authentication: GET /api/v1/account returned HTTP 401 after credential normalization. The store rejected the raw secret. Check that it belongs to this store and is active, and that its owner and namespace grants remain enabled. This response does not identify which server-side check failed.' >&2
      return 1 ;;
    403)
      printf '%s\n' '::error::Store authorization: GET /api/v1/account returned HTTP 403. The server denied account access; check token permissions and store access policy. Token formatting passed.' >&2
      return 1 ;;
    429|5??)
      printf '::error::Store authentication check: GET /api/v1/account returned HTTP %s (rate limit or server availability). Retry after the store recovers; this does not establish an invalid token.\n' "$status" >&2
      return 1 ;;
    *)
      [[ "$status" =~ ^[0-9]{3}$ ]] || status=unknown
      printf '::error::Store authentication check returned unexpected HTTP %s at GET /api/v1/account; verify the store URL and API deployment. Redirects are not followed.\n' "$status" >&2
      return 1 ;;
  esac
}

# Compare signed release metadata with the store's advertised registry authority, without granting trust.
check_store_signing_trust() {
  local document decision signer metadata="$GITHUB_WORKSPACE/$DIRECTORY/release.json"
  if ! signer=$(jq -er '.signedBy | select(type == "string" and test("^SHA256:[A-Za-z0-9+/]{43}$"))' "$metadata" 2>/dev/null); then
    printf '%s\n' '::error::Registry signing trust: signed release metadata has no valid signer fingerprint.' >&2
    return 1
  fi
  if ! document=$(curl -q --silent --show-error --fail --proto '=http,https' \
    --connect-timeout 10 --max-time 30 --url "${STORE_URL%/}/v1/keys" 2>/dev/null); then
    printf '%s\n' '::error::Registry signing trust: could not read /v1/keys. Check store availability and API deployment; no archive was uploaded.' >&2
    return 1
  fi
  if ! decision=$(printf '%s' "$document" | jq -er --slurpfile signed "$metadata" '
    $signed[0] as $release |
    if (.keys | type) != "array" or ((.revokedKeys // []) | type) != "array" then error("invalid discovery")
    elif any(.revokedKeys[]?; .id == $release.signedBy) then "revoked"
    else ([.keys[] | select(.id == $release.signedBy)] | first) as $key |
      if $key == null then "missing"
      elif (($key.scopes // []) | type) != "array" then error("invalid scopes")
      elif (($key.scopes // []) | length) == 0 or
        any($key.scopes[]; . as $prefix |
          $release.packageId == $prefix or ($release.packageId | startswith($prefix + "."))) then "trusted"
      else "scope" end
    end
  ' 2>/dev/null); then
    printf '%s\n' '::error::Registry signing trust: /v1/keys returned an invalid trust document; no archive was uploaded.' >&2
    return 1
  fi
  case "$decision" in
    trusted)
      printf 'Registry signing trust: %s is advertised for this package. Server signature verification remains authoritative.\n' "$signer" ;;
    missing)
      printf '::error::Registry signing trust: %s is absent from /v1/keys. Namespace publisher registration does not add registry trust. A store administrator must authorize this signing key in registry trust; no archive was uploaded.\n' "$signer" >&2
      return 1 ;;
    revoked)
      printf '::error::Registry signing trust: %s is revoked; no archive was uploaded.\n' "$signer" >&2
      return 1 ;;
    scope)
      printf '::error::Registry signing trust: %s is trusted outside this package namespace; no archive was uploaded.\n' "$signer" >&2
      return 1 ;;
    *) return 1 ;;
  esac
}
