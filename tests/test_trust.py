"""Check advertised registry trust without importing keys or uploading archives."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_credentials


ROOT = Path(__file__).resolve().parents[1]
SIGNER = "SHA256:" + "a" * 43
KEY = {"id": SIGNER, "publicKey": "PUBLIC_KEY_FIXTURE", "scopes": []}


class SigningTrustTests(unittest.TestCase):
    def check_trust(self, document, signer=SIGNER, curl_exit=0):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "qualified").mkdir()
            (folder / "qualified/release.json").write_text(json.dumps({
                "signedBy": signer, "packageId": "org.mass4.turian.cameras"}))
            curl = folder / "curl"
            curl.write_text("""#!/usr/bin/env python3
import os, sys
assert sys.argv[1] == '-q'
assert '-L' not in sys.argv and '--location' not in sys.argv
assert sys.argv[-1] == 'https://store.example/v1/keys'
assert '--config' not in sys.argv and '--header' not in sys.argv
sys.stdout.write(os.environ['TRUST_DOCUMENT'])
sys.stderr.write('UNTRUSTED_RESPONSE_CANARY')
sys.exit(int(os.environ['CURL_EXIT']))
""")
            curl.chmod(0o755)
            result = subprocess.run(["bash", "-euo", "pipefail", "-c",
                'source "$1"; check_store_signing_trust', "fixture", str(ROOT / "scripts/store-token.sh")],
                env={**os.environ, "PATH": str(folder) + os.pathsep + os.environ["PATH"],
                     "GITHUB_WORKSPACE": str(folder), "DIRECTORY": "qualified",
                     "STORE_URL": "https://store.example/", "TRUST_DOCUMENT": document,
                     "CURL_EXIT": str(curl_exit)}, text=True, capture_output=True)
            logs = result.stdout + result.stderr
            self.assertNotIn("UNTRUSTED_RESPONSE_CANARY", logs)
            self.assertNotIn("PUBLIC_KEY_FIXTURE", logs)
            return result, logs

    def test_global_and_matching_prefix_authority(self):
        for scopes in ([], ["org.mass4"], ["org.mass4.turian"], ["org.mass4.turian.cameras"]):
            with self.subTest(scopes=scopes):
                result, logs = self.check_trust(json.dumps({"keys": [{**KEY, "scopes": scopes}], "revokedKeys": []}))
                self.assertEqual(0, result.returncode, logs)
                self.assertIn("Server signature verification remains authoritative", logs)

    def test_missing_revoked_and_wrong_namespace_diagnostics(self):
        for document, expected in (
            ({"keys": [], "revokedKeys": []}, "Namespace publisher registration does not add registry trust"),
            ({"keys": [KEY], "revokedKeys": [{"id": SIGNER}]}, "is revoked"),
            ({"keys": [{**KEY, "scopes": ["org.mass4.turian.camera"]}]}, "outside this package namespace"),
            ({"keys": [{**KEY, "scopes": ["org.mass4x"]}]}, "outside this package namespace"),
        ):
            with self.subTest(expected=expected):
                result, logs = self.check_trust(json.dumps(document))
                self.assertNotEqual(0, result.returncode)
                self.assertIn(expected, logs)
                self.assertIn("no archive was uploaded", logs)

    def test_bad_discovery_or_metadata_fail_closed_without_echoing_response(self):
        for document in ("UNTRUSTED_RESPONSE_CANARY", "{}", '{"keys":{}}',
                         json.dumps({"keys": [{**KEY, "scopes": "org.mass4"}]})):
            result, logs = self.check_trust(document)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("invalid trust document", logs)
        result, logs = self.check_trust(json.dumps({"keys": [KEY]}), signer="bad\nfingerprint")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("no valid signer fingerprint", logs)
        result, logs = self.check_trust("", curl_exit=22)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("could not read /v1/keys", logs)

    def test_actual_upload_refuses_missing_registry_trust_before_docker(self):
        harness = test_credentials.CredentialsTests()
        result, capture, logs = harness.run_helper(json.dumps(test_credentials.EXPORT), upload=True,
            trust={"keys": [], "revokedKeys": []})
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], capture["docker"])
        self.assertIn("Namespace publisher registration does not add registry trust", logs)
