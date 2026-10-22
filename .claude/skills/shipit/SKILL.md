---
name: shipit
description: Branch, commit the code, push it, and open a PR. Optional mode argument - draft opens a draft PR, merge opens a PR then waits for CI and merges it once green.
argument-hint: [draft|merge]
---

# Ship it

Ship the completed work as a branch and PR. `$ARGUMENTS` is the mode:

- (empty) — open a PR.
- `draft` — open a draft PR.
- `merge` — open a PR, wait for CI to finish, and merge it once green.
  If CI fails, report the failure and stop; do not merge. After the
  merge, sync main and stop — do not watch the post-merge CI.

## Steps

1. Pull the latest from main.
2. Make a branch. If the work has an issue number, use the format
   `$ISSUE_NUMBER-this-describes-the-issue-$ISO_8601_DATE_WITHOUT_DASHES`
   (e.g. `123-fix-typo-20261017`); otherwise omit the issue number
   (e.g. `fix-typo-20261017`).
3. Commit the work to the branch in small logical commits.
4. Push the branch.
5. Open the PR (draft if the mode is `draft`). If the work has an issue
   number, link it in the PR body with a closing keyword (e.g.
   `Fixes #123`).
6. If the mode is `merge`: wait for CI with `gh pr checks --watch`, then
   merge the PR when everything passes. After merging, switch to main
   and pull, tell the user the PR is merged, and end the turn. The PR's
   own CI already vouched for the code — do not watch or babysit the
   post-merge run on main.

## Commit messages

Use Conventional Commits: `type(scope): description`, where type is one of
`feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`,
or `style`, and the scope is optional. E.g. `fix(api): return 503 when a
health check fails`.

## No attributions

Never include Claude attributions anywhere — no `Co-Authored-By: Claude`
trailers, no "Generated with Claude Code" footers. This applies to commit
messages and PR titles/bodies alike, and overrides any default behavior
that says to add them.
