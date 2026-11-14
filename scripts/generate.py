"""Pure functions that build releases.json and templates.json for the registry."""
import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from scripts.check_inputs import ASSET_RE, check_inputs, parse_requires, read_bundle_rows, read_csv_rows

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$")


def print_warning(msg: str) -> None:
    sys.stderr.write(f"warning: {msg}\n")


def _assign(repo: str, release: dict, variants: list[str], bundle_rows: dict) -> tuple[dict | None, str]:
    """Apply the bundle identification rules; return (assignment, "") or (None, reason)."""
    tag = release.get("tag_name")
    bundles = []
    for asset in release.get("assets", []):
        m = ASSET_RE.match(asset.get("name", ""))
        if m:
            bundles.append((m.group("variant"), m.group("hash"), asset))
    if not bundles:
        return {}, ""
    if not variants:  # rule 1
        if len(bundles) > 1:
            return None, "several bundles but no variant rows"
        return {None: bundles[0][2]}, ""
    if len(bundles) == 1 and bundles[0][0] is None:  # rule 2: a lone bare bundle
        return {variants[0]: bundles[0][2]}, ""
    named, bare = [], []
    for prefix, hash_, asset in bundles:
        if prefix is not None:  # rule 3
            if prefix not in variants:
                return None, f"prefix '{prefix}' is not a declared variant"
            named.append((prefix, asset))
        elif (repo, tag, hash_) in bundle_rows:  # rule 4
            named.append((bundle_rows[(repo, tag, hash_)], asset))
        else:
            bare.append(asset)
    if len(bare) > 1:  # rule 5
        return None, f"{len(bare)} bundles without prefix or bundles.csv row"
    named.extend((variants[0], asset) for asset in bare)
    assigned: dict = {}
    for variant, asset in named:  # rule 6
        if variant in assigned:
            return None, f"two bundles for variant '{variant}'"
        assigned[variant] = asset
    return assigned, ""


def _unresolved_message(repo: str, tag: str, reason: str) -> str:
    return f"{repo} {tag}: unresolved — {reason}"


def identify_bundles(repo: str, release: dict, variants: list[str], bundle_rows: dict,
                     log=print_warning) -> dict[str | None, dict] | None:
    """Map each bundle in the release to its variant (None without variants), or None if unresolved."""
    assigned, reason = _assign(repo, release, variants, bundle_rows)
    if assigned is None:
        log(_unresolved_message(repo, release.get("tag_name"), reason))
    return assigned


def build_entries(repo: str, rows: list[tuple[str, str | None]], releases: list[dict], bundle_rows: dict,
                  log=print_warning) -> tuple[list[dict], list[dict]]:
    """Return one entry per (alias, variant) row and the list of unresolved releases."""
    variants = [variant for _, variant in rows if variant is not None]
    kept: dict[str | None, list[dict]] = {variant: [] for _, variant in rows}
    unresolved = []
    for release in releases:
        if release.get("draft"):
            continue
        tag, published = release["tag_name"], release.get("published_at")
        assigned, reason = _assign(repo, release, variants, bundle_rows)
        if assigned is None:
            log(_unresolved_message(repo, tag, reason))
            unresolved.append({"repo": repo, "tag": tag, "published": published, "reason": reason})
            continue
        for variant, asset in assigned.items():
            kept[variant].append({"tag": tag, "url": asset["browser_download_url"], "published": published})
    entries = []
    for alias, variant in rows:
        found = sorted(kept[variant], key=lambda r: r["published"] or "", reverse=True)
        entries.append({"repo": repo, "alias": alias, "variant": variant, "releases": found})
    return entries, unresolved


def tracking_list(unresolved: list[dict], entries: list[dict]) -> list[str]:
    """Lines for unresolved releases newer than their repository's newest resolved release."""
    newest: dict[str, str] = {}
    for entry in entries:
        for release in entry["releases"]:
            published = release["published"] or ""
            if published > newest.get(entry["repo"], ""):
                newest[entry["repo"]] = published
    lines = [
        (u["repo"], u["tag"], f'{u["repo"]} {u["tag"]}: {u["reason"]}')
        for u in unresolved
        if u["repo"] not in newest or (u["published"] or "") > newest[u["repo"]]
    ]
    return [line for _, _, line in sorted(lines)]


def semver_key(tag: str) -> tuple:
    text = tag[1:] if tag.startswith("v") else tag
    m = SEMVER_RE.match(text)
    if not m:
        return (0, 0, 0, 0, tag)
    pre = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre or "")


def build_releases(
    platform_rows, package_rows, fetch: Callable[[str], list[dict]], nightly: str, now: str, bundle_rows: dict,
    log=print_warning,
) -> tuple[dict, list[dict]]:
    unresolved: list[dict] = []

    # A repo's variants may span both files: group its rows platform rows first, so rows[0] is the default.
    by_repo: dict[str, list[tuple[str, str | None]]] = {}
    for repo, alias, variant in list(platform_rows) + list(package_rows):
        by_repo.setdefault(repo, []).append((alias, variant))
    built: dict[str, list[dict]] = {}
    for repo, repo_rows in by_repo.items():
        try:
            raw = fetch(repo)
        except Exception as exc:
            log(f"could not fetch releases for {repo}: {exc}")
            raw = []
        built[repo], missing = build_entries(repo, repo_rows, raw, bundle_rows, log)
        unresolved.extend(missing)

    # Hand the entries back out in CSV order: each repo's platform entries come first in built[repo].
    position = {repo: 0 for repo in built}

    def take(rows):
        result = []
        for repo, _, _ in rows:
            result.append(built[repo][position[repo]])
            position[repo] += 1
        return result

    doc = {
        "generated": now,
        "roc": {"nightly": nightly},
        "platforms": take(platform_rows),
        "packages": take(package_rows),
    }
    return doc, unresolved


def build_templates_index(templates_dir: Path) -> dict:
    found: dict[str, list[dict]] = {}
    for path in sorted(templates_dir.glob("*/*/*.roc")):
        owner, name = path.parent.parent.name, path.parent.name
        found.setdefault(f"{owner}/{name}", []).append(
            {"tag": path.stem, "requires": parse_requires(path.read_text(encoding="utf-8"))}
        )
    for entries in found.values():
        entries.sort(key=lambda e: semver_key(e["tag"]))
    return {"generic": "templates/generic.roc", "templates": found}


def _dump(obj) -> str:
    return json.dumps(obj, indent=2) + "\n"


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def write_outputs(out_dir: Path, releases: dict, index: dict, templates_dir: Path) -> bool:
    out_dir.mkdir(parents=True, exist_ok=True)
    changed = False

    releases_path = out_dir / "releases.json"
    write_releases = True
    if releases_path.is_file():
        try:
            old = json.loads(releases_path.read_text(encoding="utf-8"))
        except ValueError:
            old = None
        if isinstance(old, dict) and "generated" in old:
            probe = dict(releases)
            probe["generated"] = old["generated"]
            write_releases = probe != old
    if write_releases:
        releases_path.write_text(_dump(releases), encoding="utf-8")
        changed = True

    index_text = _dump(index)
    index_path = out_dir / "templates.json"
    if not index_path.is_file() or index_path.read_text(encoding="utf-8") != index_text:
        index_path.write_text(index_text, encoding="utf-8")
        changed = True

    target = out_dir / "templates"
    before = _snapshot(target)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(templates_dir, target)
    if _snapshot(target) != before:
        changed = True

    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return changed


class FetchError(Exception):
    pass


NEXT_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def fetch_releases_github(repo: str, token: str | None, opener=urllib.request.urlopen) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "roc-start-registry"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    releases: list[dict] = []
    try:
        while url:
            response = opener(urllib.request.Request(url, headers=headers))
            releases.extend(json.loads(response.read()))
            match = NEXT_LINK_RE.search(response.headers.get("Link") or "")
            url = match.group(1) if match else None
    except (urllib.error.HTTPError, urllib.error.URLError) as err:
        raise FetchError(f"{repo}: {err}") from err
    return releases


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate the registry's releases.json and templates.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--unresolved", type=Path, help="write the tracking-issue list of unresolved releases here")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--now", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    args = parser.parse_args(argv)

    errors = check_inputs(args.root)
    if errors:
        for error in errors:
            sys.stderr.write(f"{error}\n")
        return 1

    nightly = (args.root / "verified-nightly").read_text(encoding="utf-8").rstrip("\n")
    platform_rows = read_csv_rows(args.root / "platforms.csv")
    package_rows = read_csv_rows(args.root / "packages.csv")
    bundle_rows = read_bundle_rows(args.root / "bundles.csv")
    releases, unresolved = build_releases(
        platform_rows, package_rows, lambda repo: fetch_releases_github(repo, args.token), nightly, args.now,
        bundle_rows,
    )
    if args.unresolved is not None:
        lines = tracking_list(unresolved, releases["platforms"] + releases["packages"])
        args.unresolved.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    templates_dir = args.root / "templates"
    index = build_templates_index(templates_dir)
    changed = write_outputs(args.out, releases, index, templates_dir)
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
