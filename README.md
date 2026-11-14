# roc-start registry

This repository lists the Roc packages and platforms that
[roc-start](https://github.com/imclerran/roc-start) can put in an app header,
along with a release URL for each and a stub-app template for each platform.
It lists only packages and platforms built for the new Zig-based Roc compiler.

A nightly GitHub Actions job crawls GitHub Releases and publishes two files to
GitHub Pages, which roc-start reads with a plain HTTP GET:

- https://www.imclerran.com/roc-start-registry/releases.json
- https://www.imclerran.com/roc-start-registry/templates.json

Template files are served at
`https://www.imclerran.com/roc-start-registry/templates/<owner>/<name>/<tag>.roc`.
The `https://imclerran.github.io/roc-start-registry/` URLs redirect to these.

## Adding a package or platform

Add one line to `packages.csv` or `platforms.csv`:

```
# repo,alias
lukewilliamboswell/roc-ansi,ansi
```

`repo` is the GitHub `owner/name`. `alias` is the shorthand used in the
generated app header (for example `import ansi.ANSI`) and must match
`^[a-z][a-z0-9_]*$`. Lines starting with `#` and a header row are ignored.
A repository without variants has exactly one row, in one of the two
files. Every alias in `platforms.csv` is `pf`; aliases in `packages.csv`
must be unique and may not be `pf`.

A repository that publishes several bundles per release (one per build or
per package) gets one row per variant instead, with an optional third column
`variant` and its own alias:

```
lukewilliamboswell/roc-ray,pf,default
lukewilliamboswell/roc-ray,pf,wayland
lukewilliamboswell/roc-graph-layout,graph,layout
lukewilliamboswell/roc-graph-layout,mermaid,mermaid
```

A repository's variants may span both files when one release ships a
platform and a package: roc-signals has the platform row
`lukewilliamboswell/roc-signals,pf,default` in `platforms.csv` and the
package row `lukewilliamboswell/roc-signals,signals,signals` in
`packages.csv`. The default variant is the repository's first row in
`platforms.csv`, or in `packages.csv` if it has no platform row. Variant
names match `^[a-z][a-z0-9_]*$`, each `(repo, variant)` pair appears once
across both files, and a repository with a variant row may not also have a
row without one in either file.

When a release's bundles carry no variant prefix in their filenames, map each
one in `bundles.csv`, using the hashes from the release notes:

```
# repo,tag,hash,variant
lukewilliamboswell/roc-ray,0.12.2,A8Dt5CorYsEtHFh8h7SfMtpzSy73UWbaB5Twy9JoYS1U,default
lukewilliamboswell/roc-ray,0.12.2,9B352s7r2amTHa93oh9UPFbAeQTvWbesEUHJhSyK8tR3,wayland
```

The hash must be 43 or 44 base58 characters, the repository must have variant
rows, and the variant must be one of them, declared in either file. Rows never
change, because hashes are permanent; once upstream prefixes its filenames,
new releases need no rows. A row is consulted only for a bare bundle that
shares its release with other bundles: a lone bare bundle always goes to the
default variant, and a prefixed bundle always follows its prefix, so a row for
either has no effect.

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
Two templates for one platform may not name the same version with and without
the `v`.

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

Each named repository must have exactly one row in `packages.csv`, since the
registry cannot yet say which of several package variants a template needs. roc-start adds those
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
      "variant": null,
      "releases": [
        { "tag": "0.28.0", "url": "https://github.com/roc-lang/basic-cli/releases/download/0.28.0/2yhxoR7cZPSYbyUzPWULFw8AA6QEcdsgFNRmzu7pZbst.tar.zst", "published": "2026-11-11T06:26:08Z" }
      ]
    }
  ],
  "packages": [ { "repo": "...", "alias": "...", "variant": null, "releases": [ ... ] } ]
}
```

There is one entry per CSV row, so a repository with variants has one entry
per variant; `variant` is the row's variant, or `null` for a repository
without variants. Releases are ordered newest first by `published`.

Only releases whose tag is a version are considered: an optional `v`, then
two or three dot-separated numbers, then an optional prerelease suffix
(`^v?\d+(?:\.\d+){1,2}(?:-[0-9A-Za-z.-]+)?$`). Releases of other artifact
streams with their own tag scheme, such as roc-ray's `types-0.9.0`, are
skipped and never tracked as unresolved; a skipped release is logged as a
warning only when it carries a `.tar.zst` asset.

Bundles are the release assets named `<hash>.tar.zst` or
`<variant>-<hash>.tar.zst`, where `<hash>` is the base58 hash the new
compiler's bundler produces (44 characters for most digests, 43 for about one
in sixteen) and the optional prefix is a variant name. A `.tar.zst` asset with
any other name is ignored with a logged warning; other assets, including
legacy `.tar.br` bundles, are ignored. Nothing is guessed: each bundle in a
release is assigned to a variant by these rules, in order, or the release is
unresolved:

1. A repository without variant rows must ship exactly one bundle per release.
2. A single bare bundle in a repository with variant rows belongs to the
   default variant.
3. A filename prefix names the variant, which must be declared for the
   repository; an undeclared prefix makes the release unresolved, even for a
   single bundle.
4. A bundle without a prefix is looked up in `bundles.csv` by repo, tag, and
   hash.
5. At most one bundle per release may be a bare hash with no row; it belongs
   to the default variant.
6. Two bundles assigned to the same variant make the release unresolved.

An unresolved release is omitted and logged with its repository, tag, and
reason. The publish workflow keeps one issue titled "Unresolved multi-bundle
releases" listing the unresolved releases that are newer than their
repository's newest resolved release, and closes it when that list is empty.
A release with no bundle is omitted. A repository with no qualifying releases
is still listed, with an empty `releases` array.

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
  compiler, and every entry's `variant` is either `null` or the name a user
  can select; the first entry for a repository is its default variant.

Nothing else about the repository layout is part of the contract. roc-start
ships the Pages URL as its default and lets `registry_url` in its config
override it.

## Maintenance

The `publish` workflow runs nightly and on `workflow_dispatch`, so the owner
can publish immediately from the Actions tab. It regenerates the outputs and
pushes to `gh-pages` only when something changed. A repository whose fetch
errors or returns 404 is logged as a warning and keeps the releases from the
previous publish (or none, if it has never been published); it never fails the
job. When every fetch fails, the job fails and nothing is written, so an API
outage cannot publish an empty registry or close the tracking issue. No Roc
toolchain is installed and no package is built.

GitHub disables scheduled workflows on public repositories after 60 days
without a commit to the default branch and emails a warning first; a quiet
registry needs the owner to re-enable the workflow or to commit occasionally.

`verified-nightly` holds one line, such as `nightly-2026-11-12-abcdef0`: the
nightly the owner last verified the ecosystem against. It is informational,
copied into `releases.json`, and edited by hand.

## Legacy

`imclerran/roc-repo` served roc-start v0.7.x. It will be archived unchanged so
its raw URLs keep resolving for legacy installs.
