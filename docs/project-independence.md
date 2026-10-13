# Project independence and publishing

## Package identity

From 0.6.0, the Python distribution is `zhihu-cli-plus`. The executable remains
`zhihu`, the import namespace remains `zhihu_cli`, and the existing local login
directory remains unchanged. Release 0.5.0 is the final Plus release using the
legacy `pyzhihu-cli` distribution name.

The new name returned HTTP 404 from PyPI's JSON API when checked on 2026-10-13.
This does not reserve it or guarantee that PyPI will accept a first upload.
GitHub Releases remain the currently verified installation channel.

## GitHub repository identity

The project is independently maintained, but the GitHub repository is still
attached to the original fork network. These are separate properties.

[GitHub's detachment documentation](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/detaching-a-fork)
states that the self-service operation loses repository metadata including
pull requests, and that the operation is permanent. The current repository
contains merged PR history and published Releases, so this operation has not
been performed.

The chosen migration on 2026-10-13 is to retain the current repository and fork
association, preserving its URL, merged PRs, Releases and other metadata.
Package identity and maintenance are independent of that GitHub association.
No repository is detached, renamed, archived or deleted in this migration.

Original authorship, history and Apache-2.0 license remain in the codebase;
see `NOTICE` and `LICENSE`. Signature-source MIT attribution remains in
`zhihu_cli/signing.py`.

## GitHub Releases

Create and push a version tag for an approved main commit. Trigger the
`GitHub Release` workflow (`release.yml`) on `main`, passing that tag.
It checks name/version, runs offline tests and Ruff, builds wheel/sdist,
validates metadata with Twine, creates checksums and publishes the release.
The changelog must contain a section for that version.

## First PyPI publication

GitHub Releases were selected as the publication channel on 2026-10-13.
PyPI publication is deferred; the manual workflow below is prepared but is not
triggered by this migration.

For a future PyPI release, use [PyPI's pending Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
from a logged-in PyPI account. Configure:

| Field | Value |
|---|---|
| PyPI project name | `zhihu-cli-plus` |
| GitHub owner | `TTAWDTT` |
| Repository | `zhihu-cli-plus` |
| Workflow filename | `publish-pypi.yml` |
| Environment | `pypi` |

Then run `Publish verified release to PyPI` on `main`, specifying an already
published GitHub Release tag. It downloads those exact artifacts, verifies
checksums and package identity, and uses OIDC to publish without a stored API
token. The workflow is manual and does not publish on ordinary pushes or PRs.
The PyPI account configuration must be completed before triggering it.

After the upload, verify the PyPI JSON API and install in a clean environment
before changing the main README to recommend `pipx install zhihu-cli-plus`.
