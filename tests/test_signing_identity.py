"""Exercise actual OpenSSH identity derivation with disposable keys."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]


class IdentityTests(unittest.TestCase):
    def test_private_key_derives_public_key_without_logging_or_leaving_key_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / 'fixture'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            private = key.read_text()
            output = root / 'output'
            environment = {**os.environ, 'SIGNING_KEY': private, 'RUNNER_TEMP': directory, 'GITHUB_OUTPUT': str(output)}
            result = subprocess.run(['bash', str(ROOT / 'scripts/signing-identity.sh')], env=environment,
                                    text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(key.with_suffix('.pub').read_text().split()[1], output.read_text())
            self.assertNotIn(private, result.stdout + result.stderr)
            self.assertEqual([], list(root.glob('brick-identity.*')))
            environment['EXPECTED_PUBLIC_KEY'] = 'ssh-ed25519 AAAA wrong'
            result = subprocess.run(['bash', str(ROOT / 'scripts/signing-identity.sh')], env=environment,
                                    text=True, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('does not match', result.stderr)
            self.assertEqual([], list(root.glob('brick-identity.*')))
