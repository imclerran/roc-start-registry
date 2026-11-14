# roc-start Registry Design

Date: 2026-11-13
Status: approved in discussion, awaiting written review
Companion: [roc-start rewrite design](2026-11-13-roc-start-rewrite-design.md)

## Purpose

The registry is the list of Roc packages and platforms that roc-start can put in
an app header, together with the release URLs for each and the stub-app
templates for each platform. It only lists packages and platforms built for the
new Zig-based Roc compiler.

It replaces `imclerran/roc-repo`, which served the legacy roc-start (v0.7.x) and
whose data model required every client to call the GitHub API through `gh` for
every repository. The new model does that work once, in CI, and clients make a
single HTTP GET.

## Goals

- Free to run and trivial to maintain for one person.
- A client fetch is one request with no authentication and no `gh` dependency.
- Adding a package is a one-line pull request.
- CI never builds or type-checks Roc code. The only compatibility signal is
  the bundle filename format, which distinguishes new-compiler bundles from
  legacy ones with no false positives.
- Legacy roc-start installs keep working: `imclerran/roc-repo` is archived
  unchanged, so its raw URLs continue to resolve.

## Non-goals

- Compatibility matrices between package releases and compiler nightlies.
  Compatibility is the compiler's job to report at build time.
- Hosting package bundles. The registry points at GitHub Release assets.
- Any per-package metadata beyond repository and alias.

## Repository

A new GitHub repository, name chosen by the owner. The design refers to it as
"the registry repo". `imclerran/roc-repo` is archived with a README pointing to
the new repository.

```
packages.csv
platforms.csv
verified-nightly                 # one line, informational, hand-edited
templates/
  generic.roc
  <owner>/<name>/<tag>.roc       # one per platform release that needs a stub
scripts/
  generate.py                    # crawl + write outputs
  check_inputs.py                # PR validation
  test_generate.py               # unittest against recorded API fixtures
  fixtures/
.github/
  workflows/
    publish.yml                  # cron + dispatch → gh-pages
    pr-checks.yml
  PULL_REQUEST_TEMPLATE.md
README.md
```

## Hand-maintained inputs

### `packages.csv` and `platforms.csv`

```
# repo,alias
lukewilliamboswell/roc-ansi,ansi
imclerran/roc-isodate,dt
```

- Columns are `repo` (GitHub `owner/name`) and `alias` (the shorthand used in
  the generated app header, e.g. `import ansi.ANSI`).
- A header row and lines beginning with `#` are allowed and ignored.
- One package or platform per line. Nothing else is recorded per entry.
- CSV was chosen over JSON because the client never reads these files; they
  exist for humans, an admission PR is one added line with a clean diff, and
  there is no trailing-comma churn. If per-entry metadata is ever needed, the
  inputs move to TOML and only `generate.py` changes.

### `verified-nightly`

One line, e.g. `nightly-2026-11-12-abcdef0`. The nightly the registry owner
last verified the listed ecosystem against. Purely informational; it is copied
into `releases.json` so roc-start can show it beside the user's installed
compiler version. CI never installs it. The name is deliberately distinct
from roc-start's own `.roc-nightly` compiler pin, which is a different thing.

### `templates/`

A template is a complete stub app for one platform, used by `roc-start app`.
Two placeholders:

- `{{dependencies}}` stands for the entire header brace block, braces
  included, so the template owns the `app [...]` provides list and roc-start
  owns everything inside the braces. Example:

  ```roc
  app [Model, init!, respond!] {{dependencies}}

  import {{pf}}.Stdout
  ```

- `{{pf}}` stands for the platform alias and may appear any number of times,
  since a stub typically imports several platform modules.

Layout and selection:

- `templates/<owner>/<name>/<tag>.roc` applies to platform `<owner>/<name>`
  at release `<tag>` and every later release until a newer template exists.
  The client picks the highest template tag that is not above the chosen
  platform release, by semver comparison of the tag strings with a leading
  `v` ignored.
- `templates/generic.roc` is used when a platform has no template at all.
  It is a minimal `app [main!] {{dependencies}}` with an empty `main!`.
- This replaces the legacy shell-script plugins. Templates are text; nothing
  is executed.

## Generated outputs

Written by CI to the `gh-pages` branch and served by GitHub Pages at the
repository's Pages URL. roc-start reads both files from there.

### `releases.json`

```json
{
  "generated": "2026-11-13T03:00:00Z",
  "roc": { "nightly": "nightly-2026-11-12-abcdef0" },
  "platforms": [
    {
      "repo": "roc-lang/basic-cli",
      "alias": "pf",
      "releases": [
        { "tag": "0.28.0", "url": "https://github.com/roc-lang/basic-cli/releases/download/0.28.0/2yhxoR7cZPSYbyUzPWULFw8AA6QEcdsgFNRmzu7pZbst.tar.zst", "published": "2026-11-11T06:26:08Z" }
      ]
    }
  ],
  "packages": [ { "repo": "...", "alias": "...", "releases": [ ... ] } ]
}
```

- `releases` is ordered newest first by `published`.
- Only assets whose filename matches `^[1-9A-HJ-NP-Za-km-z]{44}\.tar\.zst$`
  (44 base58 characters, the new compiler's bundle naming) are included.
  Legacy bundles are `.tar.br` with 43 base64url characters and are dropped.
- A release with no matching asset is omitted. A repository with no
  qualifying releases is still listed with an empty `releases` array so the
  client can tell "unknown package" from "no releases yet".
- Field names in this file are consumed by Roc code, where `package`,
  `packages`, and `platform` are reserved words. The top-level keys
  `platforms` and `packages` are therefore parsed into Roc record fields
  named `platforms` and `pkgs`; the JSON keys themselves stay readable.
  Entry fields (`repo`, `alias`, `releases`, `tag`, `url`, `published`) are
  all legal Roc field names.

### `templates.json`

```json
{
  "generic": "templates/generic.roc",
  "templates": {
    "roc-lang/basic-cli": ["0.26.0", "0.28.0"],
    "roc-lang/basic-webserver": ["0.13.0"]
  }
}
```

Tags are sorted ascending. The client resolves a template to
`<pages-url>/templates/<owner>/<name>/<tag>.roc`, so `templates/` is also
copied to `gh-pages` verbatim.

## Workflows

### `publish.yml`

Triggers: nightly cron and `workflow_dispatch`.

1. Check out `main`.
2. Run `scripts/generate.py` with the built-in `GITHUB_TOKEN`. For each repo
   in both CSVs it calls `GET /repos/{repo}/releases?per_page=100` with
   pagination, filters assets by the filename rule, and writes
   `releases.json`, `templates.json`, and a copy of `templates/` into an
   output directory. A repository that returns 404 or any other error is
   recorded as a warning in the job log and listed with an empty `releases`
   array; it never fails the job.
3. Compare the output directory with the current `gh-pages` content. Commit
   and push only when something changed, so the Pages deploy is skipped on
   quiet nights.

No Roc toolchain is installed. No package is downloaded, built, or checked.

### `pr-checks.yml`

Runs `scripts/check_inputs.py` on every pull request and fails on any of:

- A CSV line that is not `owner/name,alias` with a valid GitHub repository
  path and an alias matching `^[a-z][a-z0-9_]*$`.
- A duplicate repository or duplicate alias within a file.
- A template that does not contain `{{dependencies}}` exactly once.
- A template containing any `{{...}}` token other than `{{dependencies}}`
  or `{{pf}}` (catches typos such as `{{platform}}`).
- A template path whose `<owner>/<name>` is not a row in `platforms.csv`.
- A template filename that is not a valid semver tag with optional `v`.

### `test_generate.py`

Python `unittest` over recorded API responses in `scripts/fixtures/`,
covering: filename filtering, newest-first ordering, empty-release entries,
404 handling, and template index generation. Runs in `pr-checks.yml`.

## Admission process

1. A contributor opens a PR adding one line to `packages.csv` or
   `platforms.csv`, and optionally a template for a platform.
2. The PR template asks them to confirm the package has at least one release
   bundled with the new compiler and builds on a current nightly. This is a
   checkbox for a person, not a CI job.
3. PR checks pass; the owner reviews and merges.
4. The next nightly publish picks it up. The owner can trigger
   `workflow_dispatch` to publish immediately.

## Client contract

roc-start depends on exactly these guarantees:

- `GET <pages-url>/releases.json` and `GET <pages-url>/templates.json` return
  the shapes above.
- `GET <pages-url>/templates/generic.roc` and
  `GET <pages-url>/templates/<owner>/<name>/<tag>.roc` return template text
  for every entry in `templates.json`.
- Every `url` in `releases.json` points at a `.tar.zst` bundle for the new
  compiler.

Nothing else about the repository's layout is part of the contract. roc-start
ships the Pages URL as its default and allows `registry_url` in its config to
override it, which also makes a local fixture registry possible in tests.

## Testing and verification

- `test_generate.py` as above.
- `check_inputs.py` is itself covered by a small unittest file with one
  failing fixture per rule.
- Before archiving `roc-repo`, run the generator once by hand against the
  initial CSVs and confirm roc-start's end-to-end CI job can generate an app
  from the published output.
