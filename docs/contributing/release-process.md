# Release process

1. Update `CHANGELOG.md` — move items from `[Unreleased]` into the new version.
2. Pick the version number from the closing milestone's content:
   - If the `Changed (behavior)` section is empty, ship the milestone's patch tag (e.g. close `v0.2.1` → release `v0.2.1`).
   - If `Changed (behavior)` has any entry, bump to the next minor instead (e.g. close `v0.2.1` → release `v0.3.0`). Behaviour changes don't go out as patch releases.
3. Bump the version in `pyproject.toml` and `sdwan_mcp/__init__.py`.
4. Commit on `main` once CI is green:

    ```bash
    git commit -am "release: v0.X.Y"
    ```

5. Tag and push:

    ```bash
    git tag v0.X.Y
    git push --tags
    ```

6. The `release` workflow runs on the tag. It builds the sdist + wheel, attaches them to a GitHub release, and **publishes to PyPI via [trusted publishing](https://docs.pypi.org/trusted-publishers/)** — OIDC, no API token in the repo.

7. A companion workflow (`milestone-rollover.yml`) fires on the same tag push and performs the **milestone auto-rollover** step: it closes the milestone whose title matches the released tag and opens the next patch milestone (e.g. close `v0.2.1` → open `v0.2.2`). Re-target any leftover open issues at the new milestone.

The `docs` workflow deploys mkdocs-material to GitHub Pages on every push to `main` (and via `workflow_dispatch`).
