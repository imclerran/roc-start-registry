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
bundles.csv                      # hash → variant for releases with unprefixed bundles
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
    publish.yml                  # cron + dispatch → gh-pages, updates tracking issue
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

- Columns are `repo` (GitHub `owner/name`), `alias` (the shorthand used in
  the generated app header, e.g. `import ansi.ANSI`), and an optional third
  column `variant`.
- A header row and lines beginning with `#` are allowed and ignored.
- One entry per line. A repository that publishes one bundle per release has
  one row with no variant. A repository that publishes several bundles per
  release (roc-ray ships a default and a Wayland build; roc-signals a web
  and a GUI platform; roc-graph-layout a layout and a Mermaid package) has
  one row per variant, each with its own alias:

  ```
  lukewilliamboswell/roc-ray,pf,default
  lukewilliamboswell/roc-ray,pf,wayland
  lukewilliamboswell/roc-graph-layout,graph,layout
  lukewilliamboswell/roc-graph-layout,mermaid,mermaid
  ```

  Variant names match `^[a-z][a-z0-9_]*$`. A repository's variants are
  declared across both files: it may publish a platform and a package from
  the same release (roc-signals ships its platform as a bare bundle and a
  `signals-<hash>` tooling package), in which case it has a variant row in
  each file. The default variant is the first row for the repository in
  `platforms.csv`, or in `packages.csv` if it has no platform row. A
  repository with any variant row may not also have a row without one, in
  either file, and `(repository, variant)` is unique across both files.
- CSV was chosen over JSON because the client never reads these files; they
  exist for humans, an admission PR is one added line with a clean diff, and
  there is no trailing-comma churn. If per-entry metadata is ever needed, the
  inputs move to TOML and only `generate.py` changes.

### `bundles.csv`

Identifies bundles that carry no variant prefix in their filename (see
Bundle identification). Columns `repo,tag,hash,variant`:

```
# repo,tag,hash,variant
lukewilliamboswell/roc-ray,0.12.2,A8Dt5CorYsEtHFh8h7SfMtpzSy73UWbaB5Twy9JoYS1U,default
lukewilliamboswell/roc-ray,0.12.2,9B352s7r2amTHa93oh9UPFbAeQTvWbesEUHJhSyK8tR3,wayland
```

Rows are written by hand from the release notes, once, and never change
because hashes are permanent. The repository must have variant rows and the
variant must be one of them, in either CSV. This file is a bridge: upstream issues
(roc-ray #280, roc-signals #273) ask for prefixed filenames, after which new
releases need no rows.

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
- A stub that needs a package besides the platform (basic-webserver apps
  need `roc-lang/http` for `Response`) declares it on the template's first
  line as a Roc comment: `# requires: roc-lang/http` (comma-separated when
  several). Each named repository must be a row in `packages.csv`, so its
  release URL and alias are known; roc-start adds those packages to the
  generated header with their registry aliases, which the template's
  `import` lines therefore use. A template with no such line requires
  nothing beyond the platform.
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
      "variant": null,
      "releases": [
        { "tag": "0.28.0", "url": "https://github.com/roc-lang/basic-cli/releases/download/0.28.0/2yhxoR7cZPSYbyUzPWULFw8AA6QEcdsgFNRmzu7pZbst.tar.zst", "published": "2026-11-11T06:26:08Z" }
      ]
    }
  ],
  "packages": [ { "repo": "...", "alias": "...", "variant": null, "releases": [ ... ] } ]
}
```

- `releases` is ordered newest first by `published`.
- One entry per CSV row, so a repository with variants produces one entry
  per variant. Each entry carries `"variant"`: the variant name, or `null`
  for a repository without variants.
- Bundle assets are those whose filename matches
  `^(?:([a-z][a-z0-9_]*)-)?([1-9A-HJ-NP-Za-km-z]{43,44})\.tar\.zst$`: an
  optional variant prefix, then the base58 hash the new compiler's bundler
  produces, which is 44 characters for most 32-byte digests and 43 for the
  roughly one in sixteen whose value is small enough. A `.tar.zst` asset
  that does not match (an uppercase or hyphenated prefix, say) is logged as
  a warning so a mis-named upstream bundle is visible. Legacy bundles are `.tar.br` with 43
  base64url characters and are dropped. The compiler accepts prefixed URLs;
  roc-graph-layout already publishes `layout-<hash>` and `mermaid-<hash>`.
- A release with no bundle asset is omitted. A repository with no qualifying
  releases is still listed with an empty `releases` array so the client can
  tell "unknown package" from "no releases yet".
- When the GitHub API call for one repository fails, its entries keep the
  releases from the previous publish (read from the existing output) so a
  transient error never empties an entry; a repository that has never been
  published successfully gets an empty array. When every fetch fails, the
  generator exits non-zero and writes nothing, so an API outage cannot
  publish an empty registry or close the tracking issue.

#### Bundle identification

Only releases whose tag is a version are considered: an optional `v`, then
two or three dot-separated numbers, then an optional prerelease suffix
(`^v?\d+(?:\.\d+){1,2}(?:-[0-9A-Za-z.-]+)?$`). A repository may publish
other artifact streams under their own tag scheme (roc-ray's `types-0.9.0`
packages, roc-signals' `gui-0.1.0-rc.1`); those releases are skipped, never
assigned to a variant, and not tracked as unresolved, because they are not
releases of the listed entry at all. A skipped release that carries a bundle
asset is logged as a warning; one without any bundle (`link-inputs-*`,
`deps-*` tags) is skipped silently, so the log shows only what might have
been a listable release.

Nothing is guessed. Each bundle in a version-tagged release is assigned to
a variant by these rules, in order, or the release is unresolved:

1. A repository without variant rows must ship exactly one bundle per
   release; that bundle is the entry's release. Several bundles make the
   release unresolved (the repository needs variant rows).
2. A single bare bundle in a repository with variant rows belongs to the
   default variant (roc-ray's releases before the Wayland build existed).
3. A filename prefix names the variant, which must be one of the declared
   rows; an undeclared prefix makes the release unresolved, even for a
   single bundle, because a prefix is explicit information that must not be
   overridden.
4. A bundle without a prefix is looked up in `bundles.csv` by
   `(repo, tag, hash)`.
5. At most one bundle per release may be a bare hash with no row; it is the
   default variant. Two or more make the release unresolved.
6. Two bundles assigned to the same variant make the release unresolved.

An unresolved release is omitted from `releases.json` and logged with the
repository, tag, and reason. The publish workflow maintains one tracking
issue titled "Unresolved multi-bundle releases" listing every unresolved
release that is newer than its repository's newest resolved release, so a
new release that users cannot yet select is visible without reading logs,
while unresolved history stays out of the way. The issue is updated when the
list changes and closed when it is empty.
- Field names in this file are consumed by Roc code, where `package`,
  `packages`, and `platform` are reserved words. The top-level keys
  `platforms` and `packages` are therefore parsed into Roc record fields
  named `platforms` and `pkgs`; the JSON keys themselves stay readable.
  Entry fields (`repo`, `alias`, `variant`, `releases`, `tag`, `url`,
  `published`) are all legal Roc field names.

### `templates.json`

```json
{
  "generic": "templates/generic.roc",
  "templates": {
    "roc-lang/basic-cli": [
      { "tag": "0.26.0", "requires": [] },
      { "tag": "0.28.0", "requires": [] }
    ],
    "roc-lang/basic-webserver": [
      { "tag": "0.21.0", "requires": ["roc-lang/http"] }
    ]
  }
}
```

Entries are sorted by tag ascending (semver, leading `v` ignored,
prerelease identifiers compared numerically where numeric).
`requires` comes from the template's `# requires:` line and is empty when
the line is absent. The client resolves a template to
`<pages-url>/templates/<owner>/<name>/<tag>.roc`, so `templates/` is also
copied to `gh-pages` verbatim.

## Workflows

### `publish.yml`

Triggers: nightly cron and `workflow_dispatch`.

1. Check out `main`.
2. Run `scripts/generate.py` with the built-in `GITHUB_TOKEN`. For each repo
   in both CSVs it calls `GET /repos/{repo}/releases?per_page=100` with
   pagination, filters assets by the filename rule, assigns bundles to
   variants per Bundle identification, and writes `releases.json`,
   `templates.json`, and a copy of `templates/` into an output directory,
   plus the list of unresolved releases to a file outside it. If every
   fetch failed the step fails and the later steps do not run. A repository that returns 404 or any other error is
   recorded as a warning in the job log and keeps the releases from the
   previous publish (an empty array if it has none); one failure never
   fails the job, but every fetch failing does.
3. Compare the output directory with the current `gh-pages` content. Commit
   and push only when something changed, so the Pages deploy is skipped on
   quiet nights.
4. Create, update, or close the "Unresolved multi-bundle releases" tracking
   issue from the unresolved list (requires `issues: write`).

No Roc toolchain is installed. No package is downloaded, built, or checked.

GitHub disables scheduled workflows on public repositories after 60 days
without a commit to the default branch and emails a warning first; a quiet
registry needs the owner to re-enable it or to commit occasionally.

### `pr-checks.yml`

Runs `scripts/check_inputs.py` on every pull request and fails on any of:

- A CSV line that is not `owner/name,alias` or `owner/name,alias,variant`
  with a valid GitHub repository path, an alias matching
  `^[a-z][a-z0-9_]*$`, and a variant matching the same pattern.
- A duplicate `(repository, variant)` or duplicate alias within a file, or a
  repository that appears both with and without a variant.
- A `bundles.csv` line that is not `repo,tag,hash,variant` with a
  43- or 44-character base58 hash, a repository that has variant rows, and a
  variant declared for it; or a duplicate `(repo, tag, hash)`.
- A template that does not contain `{{dependencies}}` exactly once.
- A template containing any `{{...}}` token other than `{{dependencies}}`
  or `{{pf}}` (catches typos such as `{{platform}}`).
- A template path whose `<owner>/<name>` is not a row in `platforms.csv`.
- A template filename that is not a valid semver tag with optional `v`.
- A `# requires:` line that names a repository not in `packages.csv`, one
  with more than one package row (the registry does not yet express which
  variant is required), or that is not the first line of the template.
- Two templates for one platform whose tags name the same version with and
  without a leading `v`.
- A package alias of `pf`, which would collide with the platform alias in
  every generated header.

### `test_generate.py`

Python `unittest` over recorded API responses in `scripts/fixtures/`,
covering: filename filtering with and without prefixes, every bundle
identification rule and each unresolved case, newest-first ordering,
empty-release entries, 404 handling, the tracking-issue list, and template
index generation. Runs in `pr-checks.yml`.

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
  compiler, and every entry's `variant` is either `null` or the name a user
  can select; the first entry for a repository is its default variant.

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
