# Brick publish action

Sign qualified brick archives and upload them to a MASS4-compatible store. Most callers should use the [reusable release workflow](https://github.com/MASS4ORG/brick-workflows), which plans the build, tests archives without credentials and runs this action in an isolated publishing job.

Configure `brick-production` as a secret container restricted to `v*` tags, with no required reviewer. It needs two secrets and no variables: `BRICKS_SIGNING_KEY` and `BRICKS_STORE_ACCESS_TOKEN`. The public key is derived from the private key with `ssh-keygen -y`; the key must be unencrypted OpenSSH Ed25519. Register the derived public key with your store and grant the token `registry.publish` for your namespace.

| Input | Purpose |
|---|---|
| `directory` | Folder holding qualified archives; defaults to `qualified` |
| `archive`, `qualification` | Source archive filename and its qualification filename |
| `twin-archive`, `twin-qualification` | Optional twin archive and qualification filenames |
| `image` | Exact CLI image digest used to build the archives |
| `signing-key` | CI private key, supplied from the environment secret |
| `store-token` | Raw access token or its JSON export |
| `store-url` | Store destination; defaults to `https://bricks.mass4.org` |
| `public-key` | Optional compatibility check against the derived key; no variable is needed |

Run no brick code in this job. The action checks qualified bytes, derives the signing identity, verifies registry trust and namespace authority, authenticates the token, checks provenance/advisories, signs, then uploads source before twin. It never chooses a new build image: the planner must supply the recorded digest for a retry. A changed image or changed archive bytes is refused rather than overwriting a release.

The `readiness` sub-action supports `public-only: 'true'` for a manual preflight without secrets. Its optional public-key input enables identity-specific checks. Token/provenance checks are explicitly skipped in that mode and run during tag-time publication. Partial public success never produces `ready=true`.

Failures identify their layer. Correct the named identity, namespace, token, provenance or advisory issue; do not treat a successful public preflight as authentication proof. HTTP 401 means the token was rejected, HTTP 403 means its scope is insufficient, and transport/429/5xx failures require checking endpoint availability.

`@v1` follows compatible implementation updates. Breaking inputs use a new major version.
