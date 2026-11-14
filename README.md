# roc-start registry

This repository lists the Roc packages and platforms that
[roc-start](https://github.com/imclerran/roc-start) can put in an app header,
along with a release URL for each and a stub-app template for each platform.
It lists only packages and platforms built for the new Zig-based Roc compiler.

A nightly GitHub Actions job crawls GitHub Releases and publishes two files to
GitHub Pages, which roc-start reads with a plain HTTP GET:

- https://imclerran.github.io/roc-start-registry/releases.json
- https://imclerran.github.io/roc-start-registry/templates.json

Template files are served at
`https://imclerran.github.io/roc-start-registry/templates/<owner>/<name>/<tag>.roc`.

## Adding a package or platform

Add one line to `packages.csv` or `platforms.csv`:

```
# repo,alias
lukewilliamboswell/roc-ansi,ansi
```

`repo` is the GitHub `owner/name`. `alias` is the shorthand used in the
generated app header (for example `import ansi.ANSI`) and must match
`^[a-z][a-z0-9_]*$`. Lines starting with `#` and a header row are ignored.
Repositories and aliases must be unique within a file.

The pull request template asks you to confirm three things: the repository has
at least one release whose bundle is a `.tar.zst` built with the new Roc
compiler, you have built an app against the latest release on a current
nightly, and (for a platform) you have added a template or confirmed the
generic one suffices. PR checks validate the inputs; the owner reviews and
merges, and the next nightly publish picks the entry up.

## Adding a platform template

A template is a complete stub app for one platform, used by `roc-start app`.
Put it at `templates/<owner>/<name>/<tag>.roc`; `<owner>/<name>` must be a row
in `platforms.csv` and `<tag>` a semver tag with an optional leading `v`.

Two placeholders are recognised, and any other `{{...}}` token is an error:

- `{{dependencies}}` must appear exactly once. It stands for the whole header
  brace block, braces included, so the template owns the `app [...]` provides
  list and roc-start owns everything inside the braces.
- `{{pf}}` is the platform alias and may appear any number of times.

```roc
app [Model, init!, respond!] {{dependencies}}

import {{pf}}.Stdout
```

If the stub needs a package besides the platform, say so on the first line of
the template as a comment, comma-separated when there are several:

```roc
# requires: roc-lang/http
```

Each named repository must be a row in `packages.csv`. roc-start adds those
packages to the generated header with their registry aliases, so the
template's `import` lines use those aliases. A template without this line
requires nothing beyond the platform.

A template applies to its platform release and every later release until a
newer template exists. The client uses the highest template tag that is not
above the chosen platform release, comparing tags as semver with a leading `v`
ignored. When a platform has no template at all, `templates/generic.roc` is
used; it is a minimal `app [main!] {{dependencies}}` with an empty `main!`.

## Output file shapes

### releases.json

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

Releases are ordered newest first by `published`. Only `.tar.zst` assets with
44-character base58 names (the new compiler's bundle naming) are listed. When a
release has several such assets, the one named first in the release notes is
used, else the first in API order; the generator logs a warning for these
releases. A release with no matching asset is omitted. A repository with no
qualifying releases is still listed, with an empty `releases` array.

### templates.json

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

Entries are sorted by tag ascending (semver, leading `v` ignored). `requires`
comes from the template's `# requires:` line and is empty when absent.

## Client contract

roc-start depends on exactly these guarantees:

- `GET <pages-url>/releases.json` and `GET <pages-url>/templates.json` return
  the shapes above.
- `GET <pages-url>/templates/generic.roc` and
  `GET <pages-url>/templates/<owner>/<name>/<tag>.roc` return template text for
  every entry in `templates.json`.
- Every `url` in `releases.json` points at a `.tar.zst` bundle for the new
  compiler.

Nothing else about the repository layout is part of the contract. roc-start
ships the Pages URL as its default and lets `registry_url` in its config
override it.

## Maintenance

The `publish` workflow runs nightly and on `workflow_dispatch`, so the owner
can publish immediately from the Actions tab. It regenerates the outputs and
pushes to `gh-pages` only when something changed. A repository that errors or
returns 404 is logged as a warning and listed with no releases; it never fails
the job. No Roc toolchain is installed and no package is built.

`verified-nightly` holds one line, such as `nightly-2026-11-12-abcdef0`: the
nightly the owner last verified the ecosystem against. It is informational,
copied into `releases.json`, and edited by hand.

## Legacy

`imclerran/roc-repo` served roc-start v0.7.x. It will be archived unchanged so
its raw URLs keep resolving for legacy installs.
