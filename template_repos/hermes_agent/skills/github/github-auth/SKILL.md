---
name: github-auth
description: "GitHub auth in this sandbox is platform-managed. How git/gh/curl already work, and what to do on 401 — never set up tokens, SSH keys, or gh auth login."
version: 1.0.0
author: Humanity Rules
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [GitHub, Authentication, Git, gh-cli]
    related_skills: [github-pr-workflow, github-code-review, github-issues, github-repo-management]
---

# GitHub Authentication (platform-managed)

There is nothing to set up. The user connects their GitHub account in the
Integrations panel; a proxy inside the sandbox transparently swaps a
placeholder credential for their real, short-lived access token on HTTPS
traffic to `github.com`, `api.github.com`, and `codeload.github.com`.

- `git clone https://github.com/...`, `git push`, `gh repo list`,
  `gh pr create`, etc. just work, attributed to the connected user (not a bot).
- `GITHUB_TOKEN` is pre-set to the placeholder. It works in
  `curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/...`
  because the proxy swaps it in flight. It has no value outside the sandbox —
  never print it as if it were a secret, and never "extract" tokens from
  `~/.git-credentials` or `.env` files.

## Never do

Any of these breaks or bypasses the proxy's auth swap:

- `gh auth login`, `gh auth setup-git`, or asking the user for a personal
  access token
- changing `credential.helper` (a helper for `github.com` is pre-configured)
- SSH keys, or rewriting HTTPS remotes to SSH (`url.insteadOf`) — SSH
  bypasses the proxy and is not allowed out of the sandbox
- `~/.netrc`, embedding tokens in remote URLs, `http.sslVerify` changes

## When something fails

- 401 or "not connected": the user hasn't connected GitHub. Tell them to open
  the Integrations panel and click Connect on GitHub. Do not try to fix auth
  yourself.
- Public repos work but a private or org repo 404s: authorizing the app only
  grants public-repo access. The user must also install the GitHub App on the
  account or org that owns the repo (GitHub → Settings → Applications).

## Environment helper

`scripts/gh-env.sh` (sourced) exports `GH_AUTH_METHOD` (`gh`, `curl`, or
`none`), `GH_USER`, and — when inside a repo with a GitHub remote —
`GH_OWNER`, `GH_REPO`, `GH_OWNER_REPO`. Other GitHub skills use it as their
detection step.
