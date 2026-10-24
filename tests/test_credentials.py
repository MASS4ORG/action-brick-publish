"""Exercise the actual action helper with disposable credentials and a mock HTTP transport."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "mass4_" + "aB0_-" * 8 + "xyz"
EXPORT = {
    "id": "TOKEN_ID_CANARY",
    "secret": TOKEN,
    "expiresUtc": "2000-01-01T00:00:00Z",
    "scopes": ["OLD_SCOPE_CANARY"],
    "prefixes": ["OLD_PREFIX_CANARY"],
}


class CredentialsTests(unittest.TestCase):
    def run_helper(self, credential, status="200", curl_exit=0, upload=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mock = directory / "curl"
            mock.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps({
    'args': sys.argv[1:], 'stdin': sys.stdin.read(),
    'normalized': os.environ.get('GAYA_REGISTRY_TOKEN_STORE_EXAMPLE'),
    'github_env': pathlib.Path(os.environ['GITHUB_ENV']).read_text(),
    'github_output': pathlib.Path(os.environ['GITHUB_OUTPUT']).read_text()}))
sys.stdout.write(os.environ['HTTP_STATUS'])
sys.stderr.write('SERVER_ERROR_CANARY_' + os.environ.get('STORE_TOKEN', ''))
sys.exit(int(os.environ['CURL_EXIT']))
""")
            mock.chmod(0o755)
            docker = directory / "docker"
            docker.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
with pathlib.Path(os.environ['DOCKER_CAPTURE']).open('a') as output:
    output.write(json.dumps({'args': sys.argv[1:],
        'token': os.environ.get('GAYA_REGISTRY_TOKEN_STORE_EXAMPLE')}) + '\\n')
""")
            docker.chmod(0o755)
            for name in ("env", "output"):
                (directory / name).touch()
            environment = {
                **os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                "STORE_TOKEN": credential, "STORE_URL": "https://store.example/",
                "HTTP_STATUS": status, "CURL_EXIT": str(curl_exit),
                "CAPTURE": str(directory / "capture"),
                "GITHUB_ENV": str(directory / "env"),
                "GITHUB_OUTPUT": str(directory / "output"),
                "GITHUB_ACTION_PATH": str(ROOT), "GITHUB_WORKSPACE": str(directory),
                "RUNNER_TEMP": str(directory), "GITHUB_STEP_SUMMARY": str(directory / "summary"),
                "DIRECTORY": "qualified", "ARCHIVE": "example-1.0.0--source.brick",
                "QUALIFICATION": "qualification.json", "TWIN_ARCHIVE": "example-1.0.0--turian3.4.brick",
                "TWIN_QUALIFICATION": "twin-qualification.json",
                "TURIAN_IMAGE": "ghcr.io/example/cli@sha256:" + "0" * 64,
                "DOCKER_CAPTURE": str(directory / "docker-capture"),
            }
            script = 'source "$1"; prepare_store_token; check_store_authentication'
            if upload:
                action = (ROOT / "action.yml").read_text()
                script = textwrap.dedent(action.split("    - name: Upload\n", 1)[1].split("      run: |\n", 1)[1])
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c",
                 script, "test", str(ROOT / "scripts/store-token.sh")],
                env=environment, text=True, capture_output=True)
            capture = json.loads((directory / "capture").read_text()) if (directory / "capture").exists() else None
            docker_calls = [json.loads(line) for line in (directory / "docker-capture").read_text().splitlines()] if (directory / "docker-capture").exists() else []
            if capture is not None:
                capture["docker"] = docker_calls
            # Mask commands are consumed by GitHub, not rendered as plaintext log messages.
            lines = result.stdout.splitlines()
            masks = [line for line in lines if line.startswith("::add-mask::")]
            logs = "\n".join(line for line in lines if not line.startswith("::add-mask::")) + result.stderr
            self.assertNotIn(TOKEN, logs)
            self.assertNotIn("SERVER_ERROR_CANARY", logs)
            for canary in ("TOKEN_ID_CANARY", "OLD_SCOPE_CANARY", "OLD_PREFIX_CANARY"):
                self.assertNotIn(canary, logs)
            if capture:
                self.assertEqual(["::add-mask::" + TOKEN], masks)
                self.assertNotIn(TOKEN, json.dumps(capture["args"]))
                self.assertEqual('header = "Authorization: Bearer ' + TOKEN + '"\n', capture["stdin"])
                self.assertEqual(TOKEN, capture["normalized"])
                self.assertEqual("", capture["github_env"])
                self.assertEqual("", capture["github_output"])
                self.assertNotIn("-L", capture["args"])
                self.assertEqual("-q", capture["args"][0])
                self.assertEqual("https://store.example/api/v1/account", capture["args"][-1])
                for call in docker_calls:
                    self.assertEqual(TOKEN, call["token"])
                    self.assertNotIn(TOKEN, json.dumps(call["args"]))
            else:
                self.assertEqual([], masks)
            return result, capture, logs

    def test_raw_and_export_formats_normalize_and_mask(self):
        for value, form, trimmed in (
            (TOKEN, "raw", "false"),
            (" \t\r\n\ufeff" + TOKEN + "\r\n ", "raw", "true"),
            (json.dumps(EXPORT), "json", "false"),
            (json.dumps(EXPORT, indent=2).replace("\n", "\r\n"), "json", "false"),
            ("\ufeff " + json.dumps({**EXPORT, "secret": " \r\n" + TOKEN + "\t"}) + "\n", "json", "true"),
        ):
            with self.subTest(form=form, trimmed=trimmed):
                result, capture, logs = self.run_helper(value)
                self.assertEqual(0, result.returncode, logs)
                self.assertIsNotNone(capture)
                self.assertIn("format=" + form, logs)
                self.assertIn("normalized=" + trimmed, logs)
                self.assertIn("HTTP 200", logs)

    def test_invalid_values_never_reach_http_or_logs(self):
        values = ["", " \r\n", "{", "{}", "[]", json.dumps(TOKEN),
                  "Bearer " + TOKEN, TOKEN + "suffix", TOKEN[:20] + "\n" + TOKEN[20:],
                  "$(touch SHOULD_NOT_EXIST)", "`touch SHOULD_NOT_EXIST`"]
        for secret in (None, False, 4, [], {}, "", "  ", "secret",
                       TOKEN[:20] + "\x00" + TOKEN[20:], TOKEN[:20] + "\t" + TOKEN[20:],
                       TOKEN[:20] + "\n" + TOKEN[20:], TOKEN[:20] + "é" + TOKEN[21:]):
            values.append(json.dumps({"secret": secret}))
        for value in values:
            with self.subTest(value=value):
                result, capture, logs = self.run_helper(value)
                self.assertNotEqual(0, result.returncode)
                self.assertIsNone(capture)
                self.assertIn("Invalid store-token input", logs)
                self.assertNotIn("ignored null byte", logs)
        self.assertFalse(Path("SHOULD_NOT_EXIST").exists())

    def test_authentication_and_availability_diagnostics(self):
        for status, expected in (("401", "Store authentication:"), ("403", "Store authorization:"),
                                 ("429", "rate limit or server availability"),
                                 ("503", "rate limit or server availability"),
                                 ("302", "Redirects are not followed"),
                                 ("404", "verify the store URL")):
            with self.subTest(status=status):
                result, capture, logs = self.run_helper(json.dumps(EXPORT), status)
                self.assertNotEqual(0, result.returncode)
                self.assertIsNotNone(capture)
                self.assertIn(expected, logs)
                self.assertIn("HTTP " + status, logs)
        result, _, logs = self.run_helper(TOKEN, "000", 60)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("network, TLS or timeout", logs)
        self.assertIn("curl exit 60", logs)

    def test_actual_upload_step_passes_normalized_credentials_to_both_archives(self):
        for value in (" \r\n" + TOKEN + "\r\n", json.dumps(EXPORT, indent=2)):
            with self.subTest(value=value):
                result, capture, logs = self.run_helper(value, upload=True)
                self.assertEqual(0, result.returncode, logs)
                uploads = [call for call in capture["docker"] if "upload" in call["args"]]
                self.assertEqual(2, len(uploads))
                self.assertTrue(any("/release/example-1.0.0--source.brick" in call["args"] for call in uploads))
                self.assertTrue(any("/release/example-1.0.0--turian3.4.brick" in call["args"] for call in uploads))
        for status in ("401", "403"):
            with self.subTest(status=status):
                result, capture, logs = self.run_helper(json.dumps(EXPORT), status, upload=True)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual([], capture["docker"])
                self.assertIn("HTTP " + status, logs)


if __name__ == "__main__":
    unittest.main()
