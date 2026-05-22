# Release process

1. Update `CHANGELOG.md` — move items from `[Unreleased]` into the new version.
2. Bump the version in `pyproject.toml` and `sdwan_mcp/__init__.py`.
3. Commit on `main` once CI is green:

    ```bash
    git commit -am "release: v0.0.X"
    ```

4. Tag and push:

    ```bash
    git tag v0.0.X
    git push --tags
    ```

5. The `release` workflow builds the sdist + wheel and attaches them to a GitHub release.

The `docs` workflow deploys mkdocs-material to GitHub Pages on every push to `main` (and via `workflow_dispatch`).
