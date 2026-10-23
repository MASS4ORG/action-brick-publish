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
| `store-token`        | required                   | Publish token for the store                                                         |

`image` never defaults and never accepts a tag alone: archives are signed by the exact CLI that built them. The source is uploaded before its twin, whose `bakeOf` names it. Re-uploading identical bytes succeeds; different bytes for a published version are refused.

## Outputs

| Output         | Description                                     |
|----------------|-------------------------------------------------|
| `release`      | File name of the brick's signed release metadata |
| `twin-release` | The twin's; empty without a twin                |

## License

[MIT](LICENSE.md)
