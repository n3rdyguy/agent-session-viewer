# Vendored browser libraries

The viewer owns these runtime assets so Markdown remains available offline and does not
depend on a third-party CDN.

| Library | Version | License | Runtime file |
|---|---:|---|---|
| marked | 15.0.12 | MIT | `marked/marked.min.js` |
| DOMPurify | 3.2.6 | Apache-2.0 or MPL-2.0 | `dompurify/purify.min.js` |

The upstream license text is stored beside each distribution.

## Upgrade procedure

1. Choose and review a pinned upstream release.
2. Replace the minified distribution and license from that exact package release.
3. Update the version table above and the script paths only if upstream layout changed.
4. Run Ruff, Pyright, Pytest, and the browser security regression suite.
5. Inspect the built wheel to confirm both distributions, licenses, and this file are present.
