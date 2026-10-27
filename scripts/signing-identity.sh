#!/usr/bin/env bash
# Derive the publishing identity without requiring a separate GitHub variable.
set -euo pipefail
set +x
umask 077
key=$(mktemp "${RUNNER_TEMP:-/tmp}/brick-identity.XXXXXX")
trap 'rm -f "$key"' EXIT
printf '%s\n' "${SIGNING_KEY:?Missing signing key}" | tr -d '\r' > "$key"
public=$(ssh-keygen -y -P '' -f "$key" 2>/dev/null) || {
  echo '::error::Signing identity: expected an unencrypted OpenSSH Ed25519 private key.' >&2
  exit 1
}
[[ "$public" == ssh-ed25519\ * ]] || {
  echo '::error::Signing identity: the key must be Ed25519.' >&2
  exit 1
}
if [[ -n "${EXPECTED_PUBLIC_KEY:-}" ]]; then
  expected=$(awk '{ print $1 " " $2; exit }' <<< "$EXPECTED_PUBLIC_KEY")
  actual=$(awk '{ print $1 " " $2; exit }' <<< "$public")
  [[ "$expected" == "$actual" ]] || {
    echo '::error::Signing identity: supplied public key does not match the private key.' >&2
    exit 1
  }
fi
printf 'public-key=%s\n' "$public" >> "${GITHUB_OUTPUT:?Missing GitHub output path}"
