# Turian brick publish action

Sign qualified [Turian](https://github.com/MASS4ORG/Turian) brick archives with a publisher key and upload them to a MASS4 asset store (`https://bricks.mass4.org` by default). It checks that the archives are byte for byte the ones that were qualified before signing, and again before uploading.

This is the only step that holds the signing key and the store token. Run it in a job of its own, bound to a protected environment, that runs no brick code: no build, no tests, no scripts from the brick. [action-brick-release](https://github.com/MASS4ORG/action-brick-release) wires it that way; use that unless you need your own wiring.

## Usage

```yaml
  publish:
    needs: qualify
    runs-on: ubuntu-latest
    environment: brick-production
    steps:
      - uses: actions/download-artifact@v8
        with:
          name: qualified-brick
          path: qualified

      - uses: MASS4ORG/action-brick-publish@v1
        with:
          archive: ${{ needs.qualify.outputs.archive }}
          qualification: qualification.json
          image: ${{ needs.qualify.outputs.image }}
          signing-key: ${{ secrets.MASS4_BRICKS_CI_SIGNING_KEY }}
          public-key: ${{ vars.MASS4_BRICKS_CI_PUBLIC_KEY }}
          store-token: ${{ secrets.MASS4_BRICKS_STORE_TOKEN }}
```

The signing container uses the pinned image's configured account, which OpenSSH can resolve through its passwd database. Temporary private-key files remain owner-only inside that container; the signed release JSON is readable by the upload step.

## Inputs

| Input                | Default                    | Description                                                                         |
|----------------------|----------------------------|-------------------------------------------------------------------------------------|
| `directory`          | `qualified`                | Folder holding the archives and their qualifications                                |
| `archive`            | required                   | File name of the brick's archive                                                    |
| `qualification`      | required                   | Its qualification, binding the archive's bytes to the release tag and commit        |
| `twin-archive`       |                            | File name of the twin's archive                                                     |
| `twin-qualification` |                            | The twin's qualification                                                            |
| `image`              | required                   | The `ghcr.io/mass4org/turian-cli` image that built the archives, pinned by digest   |
| `store-url`          | `https://bricks.mass4.org` | Asset store to upload to                                                            |
| `signing-key`        | required                   | OpenSSH Ed25519 private key the store trusts for the brick's namespace              |
| `public-key`         | required                   | Its public key                                                                      |
| `store-token`        | required                   | Raw publish token or the store token JSON export                                    |

`image` never defaults and never accepts a tag alone: archives are signed by the exact CLI that built them, and the release JSON records that image and its Turian version as the release's `build`. The source is uploaded before its twin, whose `bakeOf` names it; both share the brick's id and version, and each archive's own `package.json` says which variant it is.

Before each upload the CLI reads the store's record of the same id, version and variant with the publish token. It refuses different bytes, a re-run built by another image than the one recorded, and a build that a curator advisory at `/api/v1/build-advisories` rejects, matched on the recorded build as well as this one. Identical bytes upload again; differences in metadata keep the store's first record and are reported as warnings.

The `store-token` input accepts the raw `mass4_` bearer secret or the complete JSON token export, including a pretty-printed export with newlines. The action extracts the JSON `secret` field and trims surrounding whitespace and a pasted byte-order mark. It rejects malformed JSON, missing/non-string secrets and internal whitespace before making a request. You do not need to flatten the JSON. Exported expiry, scopes and prefixes are snapshots; the store decides current validity and permissions.

The extracted secret is masked before use and reaches the CLI through its environment. Authentication is checked with a read-only `GET /api/v1/account` before publication: diagnostics distinguish local input errors, HTTP 401, HTTP 403, rate limits/server failures and network/TLS failures. No token, token metadata or response body is logged, and redirects are not followed. A successful account check confirms authentication; the release read and upload still enforce namespace and publish permissions.

Before sending an archive, the action also checks the release signer against `/v1/keys`, including revocation and package-prefix restrictions. A missing registry key is reported separately from namespace publisher registration: both must authorize the CI signer. This read does not import keys or change server/client trust. Publication still performs authoritative signature and namespace checks; the preflight cannot eliminate races or replace consumer trust configuration.

## Outputs

| Output         | Description                                     |
|----------------|-------------------------------------------------|
| `release`      | File name of the brick's signed release metadata |
| `twin-release` | The twin's; empty without a twin                |

## License

[MIT](LICENSE.md)
