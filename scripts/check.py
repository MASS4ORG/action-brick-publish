#!/usr/bin/env python3
"""Enforce action digest pins and reject executable fixed CLI defaults."""

from pathlib import Path
import json
import os
import re
import sys

USES = re.compile(r'(?:^|[\s{,])(?:[\'"]uses[\'"]|uses):\s*[\'"]?([^\s\'"#},]+)[\'"]?')
SHA = re.compile(r'[0-9a-f]{40}\Z')
VERSION_COMMENT = re.compile(r'#\s*v[0-9]+\.[0-9]+\.[0-9]+(?:\s|$)')
FIXED_CLI = re.compile(r'ghcr\.io/mass4org/turian-cli:[0-9]+\.[0-9]+\.[0-9]+|\bPINNED_(?:IMAGE|VERSION)\b')


def check(root, gitlab_template=None):
    failures = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or any(part in ('.git', '.ci', 'tests', '__pycache__') for part in path.relative_to(root).parts):
            continue
        if path.suffix not in ('.yml', '.yaml', '.sh', '.py') or path.name == 'check.py':
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            location = f'{path.relative_to(root)}:{number}'
            if FIXED_CLI.search(line):
                failures.append(f'{location}: hardcoded CLI version/default is forbidden')
            for match in ([] if line.lstrip().startswith('#') else USES.finditer(line)):
                reference = match[1]
                if reference.startswith(('./', '$/')):
                    continue
                repository, separator, revision = reference.rpartition('@')
                if repository.startswith('MASS4ORG/') and revision == 'v1':
                    continue
                if not separator or not SHA.fullmatch(revision) or not VERSION_COMMENT.search(line[match.end():]):
                    failures.append(f'{location}: third-party uses must have a full commit SHA and # vX.Y.Z comment')
    manifest = root / '.github/cli-pins.json'
    if manifest.exists():
        try:
            expected = json.loads(manifest.read_text())['gitlab-template']
            if not re.fullmatch(r'ghcr\.io/mass4org/turian-cli@sha256:[0-9a-f]{64}', expected):
                raise ValueError('invalid legitimate GitLab pin')
            template = gitlab_template or root / '.ci/Turian/ci/brick.gitlab-ci.yml'
            actual = re.findall(r'^\s+TURIAN_CLI_IMAGE:\s*(\S+)', template.read_text(), re.MULTILINE)
            if actual != [expected]:
                failures.append('GitLab template CLI digest differs from .github/cli-pins.json')
        except (OSError, ValueError, KeyError, TypeError):
            failures.append('GitLab template CLI digest or pin metadata is missing/malformed')
    return failures


if __name__ == '__main__':
    template = os.environ.get('TURIAN_GITLAB_TEMPLATE')
    errors = check(Path.cwd(), Path(template) if template else None)
    for error in errors:
        print('::error::' + error, file=sys.stderr)
    if not errors:
        print('Action SHA pins and CLI selection checks passed.')
    sys.exit(bool(errors))
