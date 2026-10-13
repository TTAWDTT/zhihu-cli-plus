# Changelog

## 0.6.0

- Python distribution renamed from `pyzhihu-cli` to `zhihu-cli-plus`.
- Keep the `zhihu` command, `zhihu_cli` Python namespace and existing login directory.
- Update project branding, GitHub badges, installation and migration instructions.
- Retain upstream authorship/history, Apache-2.0 license and signing attributions.
- Prepare manual PyPI Trusted Publishing and package metadata checks.
- Retain the GitHub fork association to preserve PR, Release and repository history.
- Use GitHub Releases; defer PyPI publication until future account configuration.

## 0.5.0

- Add signed root/child comment likes and cancellation through `like-comment --undo`.
- Add offline previews and JSON results; show the current account's like state.
- Validate exact targets, explicit acknowledgements and uncertain delivery.
- Verify one approved like/readback/unlike/readback lifecycle with restored state.

## 0.4.1

- Fix empty V5 root-comment reads caused by the literal zero initial cursor.
- Group IDs and bodies; show inline replies, reply targets and remaining-reply hints.

## 0.4.0

- Add comment publishing, exact-target replies, comment detail, replies and deletion.
- Sign exact UTF-8 request bytes, preserve moderation fields and never retry writes.

## 0.3.0

- Add JSON output, comment reads and bounded server-cursor pagination.
- Fix content/publication bugs and add bounded signed GET fallback for code 10003.

The original codebase was maintained as `pyzhihu-cli` by
[BAIGUANGMEI/zhihu-cli](https://github.com/BAIGUANGMEI/zhihu-cli).
