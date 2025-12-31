# Publishing in HACS (guide for maintainers)

This repository is a Home Assistant **custom integration** (`custom_components/tylo_sauna`).

## Why updates may not show up (custom repository)

HACS update notifications are usually based on **GitHub Releases/Tags**, not just new commits on `main`.
If the repository does not publish releases, users may end up “stuck” on an older version until they manually re-download.

## Recommended release flow

1. Update version in `custom_components/tylo_sauna/manifest.json` (`"version": "X.Y.Z"`).
2. Update `CHANGELOG.md`.
3. Create and push a tag `vX.Y.Z` (example: `v0.3.3`).
4. GitHub Actions will:
   - validate the repository (HACS Action + hassfest)
   - publish a GitHub Release
   - attach `tylo_sauna.zip` (contains `custom_components/tylo_sauna`)

Once the release is published, HACS should detect the new version and offer an update.

## Getting into the default HACS store (no custom repository)

To avoid “Custom repositories” installation, the integration must be added to the HACS default list:

- Ensure the repository is public and passes the GitHub Actions checks:
  - HACS Action (`.github/workflows/hacs.yml`)
  - hassfest (`.github/workflows/hassfest.yml`)
- Create at least one GitHub Release.
- Submit a PR to `hacs/default` to include this repository as an integration.

Official guide: see HACS docs “Publish → Include”.


