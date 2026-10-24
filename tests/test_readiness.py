"""Exercise publish readiness against a mock store and registry; no request leaves the machine."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

from test_credentials import TOKEN


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "org.mass4.turian.cameras"
DIGEST = "sha256:" + "c" * 64
PINNED = "ghcr.io/mass4org/turian-cli:3.4.0@" + DIGEST
IMAGE = "ghcr.io/mass4org/turian-cli@" + DIGEST
CANARY = "RESPONSE_BODY_CANARY"


class ReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        key = Path(cls.keys.name) / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "fixture", "-f", str(key)], check=True)
        cls.public = (key.with_suffix(".pub")).read_text().strip()
        cls.fingerprint = subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", str(key.with_suffix(".pub"))],
            check=True, text=True, capture_output=True).stdout.split()[1]

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def routes(self, **overrides):
        """A store where every layer is ready for a first publication."""
        routes = {
            "/v1/keys": [200, {"keys": [{"id": self.fingerprint, "publicKey": "x", "scopes": []}], "revokedKeys": []}],
            "/v1/index.json": [200, {"bricks": {}, "claims": {
                "org.mass4": {"key": self.fingerprint, "keys": [self.fingerprint]}}}],
            "/api/v1/account": [200, {"email": CANARY, "prefixes": ["org.mass4"]}],
            f"/api/v1/releases/{PACKAGE}/1.0.2?variant=source": [404, {"detail": CANARY}],
            f"/api/v1/releases/{PACKAGE}/1.0.2?variant=turian3.4": [404, {"detail": CANARY}],
            "/api/v1/build-advisories": [200, []],
        }
        routes.update(overrides)
        return routes

    def run_readiness(self, routes=None, image=PINNED, token=TOKEN, public=None, resolved=DIGEST, label="3.4.0",
                      script=None, extra=None):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            curl = folder / "curl"
            curl.write_text("""#!/usr/bin/env python3
import json, os, sys
arguments = sys.argv[1:]
assert arguments[0] == '-q' and '-L' not in arguments and '--location' not in arguments
url = arguments[arguments.index('--url') + 1]
output = arguments[arguments.index('--output') + 1]
header = sys.stdin.read()
with open(os.environ['CURL_LOG'], 'a') as log:
    log.write(json.dumps({'url': url, 'authenticated': 'Bearer' in header, 'args': arguments}) + '\\n')
path = url.removeprefix('https://store.example')
routes = json.loads(os.environ['ROUTES'])
if path not in routes:
    sys.stdout.write('404')
    sys.exit(0)
status, body = routes[path]
if status == 0:
    sys.exit(7)
with open(output, 'w') as file:
    file.write(body if isinstance(body, str) else json.dumps(body))
sys.stdout.write(str(status))
""")
            docker = folder / "docker"
            docker.write_text("""#!/usr/bin/env python3
import os, sys
arguments = sys.argv[1:]
if arguments[0] == 'pull':
    sys.exit(1 if arguments[-1] in os.environ['PULL_FAILS'].split() else 0)
if arguments[:2] == ['image', 'inspect']:
    if 'RepoDigests' in arguments[3]:
        print('ghcr.io/mass4org/turian-cli@' + os.environ['RESOLVED'])
    else:
        print(os.environ['LABEL'])
""")
            for tool in (curl, docker):
                tool.chmod(0o755)
            (folder / "qualified").mkdir()
            (folder / "qualified/qualification.json").write_text(json.dumps({"packageId": PACKAGE, "version": "1.0.2"}))
            (folder / "package.json").write_text(json.dumps({"id": PACKAGE, "version": "1.0.2"}))
            for name in ("summary", "output", "curl-log"):
                (folder / name).touch()
            environment = {
                **os.environ, "PATH": str(folder) + os.pathsep + os.environ["PATH"],
                "ROUTES": json.dumps(routes if routes is not None else self.routes()),
                "CURL_LOG": str(folder / "curl-log"), "PULL_FAILS": "", "RESOLVED": resolved, "LABEL": label,
                "STORE_URL": "https://store.example/", "STORE_TOKEN": token,
                "PUBLIC_KEY": self.public if public is None else public, "IMAGE": image,
                "PACKAGE_ID": PACKAGE, "PACKAGE_VERSION": "1.0.2",
                "RUNNER_TEMP": str(folder), "GITHUB_WORKSPACE": str(folder), "GITHUB_ACTION_PATH": str(ROOT),
                "GITHUB_STEP_SUMMARY": str(folder / "summary"), "GITHUB_OUTPUT": str(folder / "output"),
                **(extra or {}),
            }
            body = script or 'source "$1"; source "$2"; readiness_run'
            result = subprocess.run(["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", body, "test",
                str(ROOT / "scripts/store-token.sh"), str(ROOT / "scripts/readiness.sh")],
                env=environment, text=True, capture_output=True)
            lines = result.stdout.splitlines()
            logs = "\n".join(line for line in lines if not line.startswith("::add-mask::")) + result.stderr
            summary = (folder / "summary").read_text()
            requests = [json.loads(line) for line in (folder / "curl-log").read_text().splitlines()]
            outputs = (folder / "output").read_text()
            self.assertNotIn(TOKEN, logs + summary + outputs)
            self.assertNotIn(CANARY, logs + summary)
            self.assertNotIn(TOKEN, json.dumps([request["args"] for request in requests]))
            self.assertEqual([], list(folder.glob("readiness.*")), "scratch files must be removed")
            return result, logs, summary, outputs, requests

    def assert_not_ready(self, result, logs, summary, *expected):
        self.assertNotEqual(0, result.returncode, logs)
        self.assertIn("Publish readiness: not ready", summary)
        for text in expected:
            self.assertIn(text, logs)

    def test_first_publication_is_ready_with_combined_result_and_timestamp(self):
        result, logs, summary, outputs, requests = self.run_readiness()
        self.assertEqual(0, result.returncode, logs)
        self.assertIn("Publish readiness: ready", summary)
        self.assertIn("ready=true", outputs)
        self.assertRegex(outputs, r"checked-at=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
        self.assertIn("returned HTTP 404", logs)
        self.assertIn("tag 3.4.0 resolves to the pinned digest", logs)
        authenticated = {request["url"].removeprefix("https://store.example") for request in requests
                         if request["authenticated"]}
        self.assertEqual({"/api/v1/account", f"/api/v1/releases/{PACKAGE}/1.0.2?variant=source",
                          "/api/v1/build-advisories"}, authenticated)

    def test_known_production_mismatch_names_the_missing_registry_layer(self):
        routes = self.routes(**{"/v1/keys": [200, {"keys": [], "revokedKeys": []}]})
        result, logs, summary, outputs, requests = self.run_readiness(routes)
        self.assert_not_ready(result, logs, summary,
            f"{self.fingerprint} is absent from /v1/keys. Namespace publisher registration does not add registry trust",
            "Registry:TrustedPublicKeys")
        self.assertIn("ready=false", outputs)
        self.assertIn("is a publisher key of namespace org.mass4", logs)
        self.assertIn("1 failing checks", logs)

    def test_namespace_layer_is_checked_independently_of_registry_trust(self):
        for claims, expected in (
            ({"org.mass4": {"key": "SHA256:" + "b" * 43, "keys": ["SHA256:" + "b" * 43]}},
             "is not a publisher key of namespace org.mass4"),
            # A more specific claim governs even when a parent claim lists the key.
            ({"org.mass4": {"keys": [self.fingerprint]}, "org.mass4.turian": {"keys": ["SHA256:" + "b" * 43]}},
             "is not a publisher key of namespace org.mass4.turian"),
            ({"com.example": {"keys": [self.fingerprint]}}, "No active namespace claim"),
            ({}, "No active namespace claim"),
        ):
            with self.subTest(expected=expected):
                routes = self.routes(**{"/v1/index.json": [200, {"claims": claims}]})
                result, logs, summary, _, _ = self.run_readiness(routes)
                self.assert_not_ready(result, logs, summary, expected,
                    "Registry trust does not grant namespace publisher authority" if "publisher key" in expected
                    else "/account/namespaces")
                self.assertIn("is in registry signature trust", logs)
        routes = self.routes(**{"/v1/index.json": [200, {"claims": {"org.mass4": {"key": self.fingerprint}}}]})
        result, logs, _, _, _ = self.run_readiness(routes)
        self.assertEqual(0, result.returncode, logs)

    def test_revoked_or_out_of_scope_signer_and_wrong_token_namespace(self):
        key = {"id": self.fingerprint, "publicKey": "x"}
        for routes, expected in (
            (self.routes(**{"/v1/keys": [200, {"keys": [{**key, "scopes": []}], "revokedKeys": [{"id": self.fingerprint}]}]}),
             "is revoked"),
            (self.routes(**{"/v1/keys": [200, {"keys": [{**key, "scopes": ["org.mass4.turian.camera"]}]}]}),
             "its scopes exclude " + PACKAGE),
            (self.routes(**{"/api/v1/account": [200, {"email": CANARY, "prefixes": ["org.mass4x"]}]}),
             "is scoped to [org.mass4x], which does not cover " + PACKAGE),
        ):
            with self.subTest(expected=expected):
                result, logs, summary, _, _ = self.run_readiness(routes)
                self.assert_not_ready(result, logs, summary, expected)

    def test_advisory_policy(self):
        def advisory(**fields):
            return {"id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427", "packagePrefix": "org.mass4", "message": CANARY,
                    "image": None, "turianVersionRange": None, "reject": True, **fields}
        for advisories, ready, expected in (
            ([advisory(image=IMAGE)], False, "rejects builds by " + IMAGE),
            ([advisory(turianVersionRange=">=3.0.0 <3.5.0")], False, "readiness does not evaluate ranges"),
            ([advisory(image=IMAGE, reject=False)], True, "warns for builds by"),
            ([advisory(image="ghcr.io/mass4org/turian-cli@sha256:" + "d" * 64)], True, "no advisory rejects"),
            ([advisory(image=IMAGE, packagePrefix="org.mass4.turian.camera")], True, "no advisory rejects"),
        ):
            with self.subTest(expected=expected):
                routes = self.routes(**{"/api/v1/build-advisories": [200, advisories]})
                result, logs, summary, _, _ = self.run_readiness(routes)
                self.assertIn(expected, logs)
                self.assertIn("/api/v1/build-advisories/1b4e28ba" if "no advisory" not in expected else "HTTP 200", logs)
                self.assertEqual(ready, result.returncode == 0, logs)
        for status, body in ((200, {"advisories": []}), (200, CANARY), (503, CANARY)):
            routes = self.routes(**{"/api/v1/build-advisories": [status, body]})
            result, logs, summary, _, _ = self.run_readiness(routes)
            self.assert_not_ready(result, logs, summary, "build-advisories")

    def test_bad_image_pins_fail_with_the_pinned_and_resolved_digests(self):
        result, logs, summary, _, _ = self.run_readiness(resolved="sha256:" + "e" * 64)
        self.assert_not_ready(result, logs, summary, "Tag 3.4.0 resolves to sha256:" + "e" * 64,
                              "the workflow pins " + DIGEST)
        result, logs, summary, _, _ = self.run_readiness(label="3.4.1")
        self.assert_not_ready(result, logs, summary, "carries version label '3.4.1', expected '3.4.0'")
        for image in ("ghcr.io/mass4org/turian-cli:3.4.0", "ghcr.io/other/turian-cli@" + DIGEST):
            result, logs, summary, _, _ = self.run_readiness(image=image)
            self.assert_not_ready(result, logs, summary, "is not ghcr.io/mass4org/turian-cli pinned by sha256 digest")
        result, logs, summary, _, _ = self.run_readiness(extra={"PULL_FAILS": IMAGE})
        self.assert_not_ready(result, logs, summary, "The registry does not serve " + IMAGE)

    def test_provenance_reads(self):
        receipt = {"id": "x", "packageId": PACKAGE, "version": "1.0.2", "variant": "source",
                   "integrity": "sha256-x", "status": "pending", "build": {"image": IMAGE, "turianVersion": "3.4.0"}}
        path = f"/api/v1/releases/{PACKAGE}/1.0.2?variant=source"
        for status, body, ready, expected in (
            (200, receipt, True, "an identical re-run is accepted"),
            (200, {**receipt, "build": None}, True, "an identical re-run is accepted"),
            (200, {**receipt, "build": {"image": "ghcr.io/mass4org/turian-cli@sha256:" + "f" * 64}}, False,
             "the store refuses a re-run with " + IMAGE),
            (200, CANARY, False, "malformed release receipt"),
            (200, {"status": "pending"}, False, "malformed release receipt"),
            (401, CANARY, False, "HTTP 401: the store rejected the publish token"),
            (403, CANARY, False, "HTTP 403"),
            (302, CANARY, False, "redirects are not followed"),
            (503, CANARY, False, "says nothing about the credential"),
            (0, "", False, "network, TLS or timeout"),
        ):
            with self.subTest(status=status, expected=expected):
                result, logs, summary, _, _ = self.run_readiness(self.routes(**{path: [status, body]}))
                self.assertIn(expected, logs)
                self.assertEqual(ready, result.returncode == 0, logs)

    def test_malformed_token_and_key_fail_without_sending_credentials(self):
        result, logs, summary, _, requests = self.run_readiness(token="Bearer " + TOKEN)
        self.assert_not_ready(result, logs, summary, "no credential was sent")
        self.assertFalse(any(request["authenticated"] for request in requests))
        self.assertIn("is in registry signature trust", logs)
        for public in ("", "ssh-rsa AAAAB3NzaC1yc2E fixture", "not a key"):
            result, logs, summary, _, _ = self.run_readiness(public=public)
            self.assert_not_ready(result, logs, summary, "not a single OpenSSH ssh-ed25519 public key")

    def test_readiness_action_reads_the_manifest(self):
        action = (ROOT / "readiness/action.yml").read_text()
        script = textwrap.dedent(action.split("      run: |\n", 1)[1])
        script = script.replace('"$GITHUB_ACTION_PATH/../scripts/store-token.sh"', '"$1"')
        script = script.replace('"$GITHUB_ACTION_PATH/../scripts/readiness.sh"', '"$2"')
        result, logs, _, _, requests = self.run_readiness(script=script,
            extra={"PACKAGE_ID": "", "PACKAGE_VERSION": "", "BRICK_PATH": ".", "VARIANTS": "source"})
        self.assertEqual(0, result.returncode, logs)
        self.assertIn(f"Publish readiness for {PACKAGE} 1.0.2", logs)

    def test_publish_action_checks_source_and_twin_before_signing(self):
        action = (ROOT / "action.yml").read_text()
        step = action.split("    - name: Check publish readiness\n", 1)[1].split("    - name: Sign\n", 1)[0]
        self.assertLess(action.index("- name: Check publish readiness"), action.index("- name: Sign"))
        self.assertNotIn("signing-key", step)
        script = textwrap.dedent(step.split("      run: |\n", 1)[1])
        script = script.replace('"$GITHUB_ACTION_PATH/scripts/store-token.sh"', '"$1"')
        script = script.replace('"$GITHUB_ACTION_PATH/scripts/readiness.sh"', '"$2"')
        result, logs, _, _, requests = self.run_readiness(image=IMAGE, script=script, extra={
            "DIRECTORY": "qualified", "QUALIFICATION": "qualification.json",
            "TWIN_ARCHIVE": f"{PACKAGE}-1.0.2--turian3.4.brick", "READINESS_VERIFIED_VERSION": "3.4.0",
            "PACKAGE_ID": "", "PACKAGE_VERSION": ""})
        self.assertEqual(0, result.returncode, logs)
        urls = [request["url"] for request in requests]
        self.assertIn(f"https://store.example/api/v1/releases/{PACKAGE}/1.0.2?variant=turian3.4", urls)
        self.assertIn(f"https://store.example/api/v1/releases/{PACKAGE}/1.0.2?variant=source", urls)


if __name__ == "__main__":
    unittest.main()
