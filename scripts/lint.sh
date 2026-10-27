#!/usr/bin/env bash
# Validate both workflow syntax and executable shell scripts.
set -euo pipefail
temporary=$(mktemp -d "${RUNNER_TEMP:-/tmp}/brick-lint.XXXXXX")
trap 'rm -rf "$temporary"' EXIT
if [[ -z "${ACTIONLINT:-}" ]]; then
  archive="$temporary/actionlint.tar.gz"
  curl --fail --silent --show-error --location \
    https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz \
    --output "$archive"
  printf '%s  %s\n' 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 "$archive" | sha256sum --check --status
  tar -xzf "$archive" -C "$temporary" actionlint
  ACTIONLINT="$temporary/actionlint"
fi
"$ACTIONLINT" .github/workflows/*.yml
if compgen -G 'scripts/*.sh' > /dev/null; then
  shellcheck scripts/*.sh
fi
