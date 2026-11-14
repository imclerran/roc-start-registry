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

from scripts.check_inputs import ASSET_RE, check_inputs, parse_requires, read_csv_rows

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$")


def print_warning(msg: str) -> None:
    sys.stderr.write(f"warning: {msg}\n")


def pick_asset(release: dict, log=print_warning) -> dict | None:
    """Return the first asset named like a bundle, warning if several match."""
    matches = [a for a in release.get("assets", []) if ASSET_RE.match(a.get("name", ""))]
    if not matches:
        return None
    if len(matches) > 1:
        log(f'release {release.get("tag_name")}: {len(matches)} matching assets, using the first')
    return matches[0]


def build_entry(repo: str, alias: str, releases: list[dict], log=print_warning) -> dict:
    kept = []
    for release in releases:
        if release.get("draft"):
            continue
        asset = pick_asset(release, log)
        if asset is None:
            continue
        kept.append(
            {
                "tag": release["tag_name"],
                "url": asset["browser_download_url"],
                "published": release.get("published_at"),
            }
        )
    kept.sort(key=lambda r: r["published"] or "", reverse=True)
    return {"repo": repo, "alias": alias, "releases": kept}


def semver_key(tag: str) -> tuple:
    text = tag[1:] if tag.startswith("v") else tag
    m = SEMVER_RE.match(text)
    if not m:
        return (0, 0, 0, 0, tag)
    pre = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre or "")


def build_releases(
    platform_rows, package_rows, fetch: Callable[[str], list[dict]], nightly: str, now: str, log=print_warning
) -> dict:
    def entries(rows):
        result = []
        for repo, alias in rows:
            try:
                raw = fetch(repo)
            except Exception as exc:
                log(f"could not fetch releases for {repo}: {exc}")
                raw = []
            result.append(build_entry(repo, alias, raw, log))
        return result

    return {
        "generated": now,
        "roc": {"nightly": nightly},
        "platforms": entries(platform_rows),
        "packages": entries(package_rows),
    }


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
    releases = build_releases(
        platform_rows, package_rows, lambda repo: fetch_releases_github(repo, args.token), nightly, args.now
    )
    templates_dir = args.root / "templates"
    index = build_templates_index(templates_dir)
    changed = write_outputs(args.out, releases, index, templates_dir)
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
