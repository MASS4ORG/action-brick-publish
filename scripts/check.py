#!/usr/bin/env python3
"""Enforce action digest pins and reject executable fixed CLI defaults."""

from pathlib import Path
import re
import sys

USES = re.compile(r'^\s*(?:-\s*)?uses:\s*[\'"]?([^\s\'"#]+)[\'"]?(.*)$')
SHA = re.compile(r'[0-9a-f]{40}\Z')
VERSION_COMMENT = re.compile(r'#\s*v[0-9]+\.[0-9]+\.[0-9]+(?:\s|$)')
FIXED_CLI = re.compile(r'ghcr\.io/mass4org/turian-cli:[0-9]+\.[0-9]+\.[0-9]+|\bPINNED_(?:IMAGE|VERSION)\b')


def check(root):
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
            match = USES.match(line)
            if not match:
                continue
            reference, comment = match.groups()
            if reference.startswith(('./', '$/')):
                continue
            repository, separator, revision = reference.rpartition('@')
            if repository.startswith('MASS4ORG/') and revision == 'v1':
                continue
            if not separator or not SHA.fullmatch(revision) or not VERSION_COMMENT.search(comment):
                failures.append(f'{location}: third-party uses must have a full commit SHA and # vX.Y.Z comment')
    return failures


if __name__ == '__main__':
    errors = check(Path.cwd())
    for error in errors:
        print('::error::' + error, file=sys.stderr)
    if not errors:
        print('Action SHA pins and CLI selection checks passed.')
    sys.exit(bool(errors))
