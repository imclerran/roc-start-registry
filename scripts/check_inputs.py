"""Validate the registry inputs: CSV lists, templates, and verified-nightly."""
import re
import sys
from collections import Counter
from pathlib import Path

ASSET_RE = re.compile(r"^(?:(?P<variant>[a-z][a-z0-9_]*)-)?(?P<hash>[1-9A-HJ-NP-Za-km-z]{43,44})\.tar\.zst$")
HASH_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{43,44}$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+$")
TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
# A release tag that names a version; other tags are separate artifact streams (roc-ray's `types-0.9.0`).
RELEASE_TAG_RE = re.compile(r"^v?\d+(?:\.\d+){1,2}(?:-[0-9A-Za-z.-]+)?$")
NIGHTLY_RE = re.compile(r"^nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]{7}$")
PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")
REQUIRES_RE = re.compile(
    r"^# requires: ([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)(, [A-Za-z0-9._-]+/[A-Za-z0-9._-]+)*$"
)

REQUIRES_PREFIX = "# requires:"
CSV_HEADERS = ("repo,alias", "repo,alias,variant")
BUNDLES_HEADER = "repo,tag,hash,variant"


def _data_lines(path: Path, headers: tuple[str, ...]) -> list[tuple[int, list[str]]]:
    """Return (line number, stripped fields), skipping blanks, comments, and header rows."""
    rows = []
    text = path.read_text(encoding="utf-8")
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line in headers:
            continue
        rows.append((number, [field.strip() for field in line.split(",")]))
    return rows


def _csv_rows_with_lines(path: Path) -> list[tuple[int, list[str]]]:
    return _data_lines(path, CSV_HEADERS)


def read_csv_rows(path: Path) -> list[tuple[str, str, str | None]]:
    """Return (repo, alias, variant) rows; variant is None when the column is absent."""
    rows = []
    for _, fields in _csv_rows_with_lines(path):
        fields = fields + [""] * (2 - len(fields))
        rows.append((fields[0], fields[1], fields[2] if len(fields) > 2 else None))
    return rows


def read_bundle_rows(path: Path) -> dict[tuple[str, str, str], str]:
    """Map (repo, tag, hash) to variant from bundles.csv; a missing file gives {}."""
    if not path.is_file():
        return {}
    return {
        (fields[0], fields[1], fields[2]): fields[3]
        for _, fields in _data_lines(path, (BUNDLES_HEADER,))
        if len(fields) == 4
    }


def parse_requires(text: str) -> list[str]:
    """Return repos named on a line-1 `# requires:` line, else []."""
    first = text.split("\n", 1)[0].rstrip("\r")
    if not REQUIRES_RE.match(first):
        return []
    return [r.strip() for r in first[len("# requires:"):].split(",")]


def _check_csv(root: Path, name: str, is_platform: bool, errors: list[str],
               variants: dict[str, list], seen: dict[tuple[str, str | None], str]) -> Counter:
    """Validate a CSV list and return its repos with their row counts.

    `variants` (repo -> variants in order, [None] without variants) and `seen` ((repo, variant) -> where
    first seen) are shared across both files, since a repo's variants may span them.
    """
    path = root / name
    if not path.is_file():
        errors.append(f"{name}: file is missing")
        return Counter()
    repos: Counter = Counter()
    aliases: dict[str, int] = {}
    for number, fields in _csv_rows_with_lines(path):
        if len(fields) not in (2, 3):
            errors.append(f"{name}:{number}: expected repo,alias or repo,alias,variant")
            continue
        repo, alias = fields[0], fields[1]
        variant = fields[2] if len(fields) == 3 else None
        if not REPO_RE.match(repo):
            errors.append(f'{name}:{number}: repo "{repo}" does not match {REPO_RE.pattern}')
        if not ALIAS_RE.match(alias):
            errors.append(f'{name}:{number}: alias "{alias}" does not match {ALIAS_RE.pattern}')
        if variant is not None and not ALIAS_RE.match(variant):
            errors.append(f'{name}:{number}: variant "{variant}" does not match {ALIAS_RE.pattern}')
        if is_platform and alias != "pf":
            errors.append(f'{name}:{number}: platform alias "{alias}" must be "pf"')
        if not is_platform and alias == "pf":
            errors.append(f'{name}:{number}: package alias "pf" is reserved for the platform')
        repos[repo] += 1
        key = (repo, variant)
        if key in seen:
            what = f'repo "{repo}"' if variant is None else f'repo "{repo}" variant "{variant}"'
            errors.append(f"{name}:{number}: duplicate {what} (first on {seen[key]})")
        elif repo in variants and (variant is None) != (variants[repo][0] is None):
            errors.append(f'{name}:{number}: repo "{repo}" appears both with and without a variant')
        else:
            seen[key] = f"{name} line {number}"
            variants.setdefault(repo, []).append(variant)
        if not is_platform and alias in aliases:
            errors.append(f'{name}:{number}: duplicate alias "{alias}" (first on line {aliases[alias]})')
        else:
            aliases.setdefault(alias, number)
    return repos


def _check_bundles(root: Path, variants: dict[str, list], errors: list[str]) -> None:
    path = root / "bundles.csv"
    if not path.is_file():
        return
    seen: dict[tuple[str, str, str], int] = {}
    for number, fields in _data_lines(path, (BUNDLES_HEADER,)):
        where = f"bundles.csv:{number}:"
        if len(fields) != 4:
            errors.append(f"{where} expected repo,tag,hash,variant")
            continue
        repo, tag, hash_, variant = fields
        if not HASH_RE.match(hash_):
            errors.append(f'{where} hash "{hash_}" is not 43 or 44 base58 characters')
        declared = [v for v in variants.get(repo, []) if v is not None]
        if not declared:
            errors.append(f'{where} repo "{repo}" has no variant rows in packages.csv or platforms.csv')
        elif variant not in declared:
            errors.append(f'{where} variant "{variant}" is not declared for "{repo}"')
        key = (repo, tag, hash_)
        if key in seen:
            errors.append(f"{where} duplicate bundle {repo} {tag} {hash_} (first on line {seen[key]})")
        else:
            seen[key] = number


def _check_template(root: Path, path: Path, packages: Counter, errors: list[str]) -> None:
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    tokens = PLACEHOLDER_RE.findall(text)
    count = tokens.count("{{dependencies}}")
    if count != 1:
        errors.append(f"{rel}: {{{{dependencies}}}} must appear exactly once, found {count}")
    for token in sorted(set(tokens) - {"{{dependencies}}", "{{pf}}"}):
        errors.append(f"{rel}: unknown placeholder {token}")
    lines = text.splitlines()
    if lines and lines[0].startswith(REQUIRES_PREFIX):
        if not REQUIRES_RE.match(lines[0]):
            errors.append(f"{rel}:1: malformed requires line")
        for repo in parse_requires(text):
            if repo not in packages:
                errors.append(f'{rel}:1: required package "{repo}" is not in packages.csv')
            elif packages[repo] > 1:
                errors.append(f"{rel}:1: requires {repo} which has several package variants; "
                              "the registry cannot express which")
    for number, line in enumerate(lines[1:], start=2):
        if line.startswith(REQUIRES_PREFIX):
            errors.append(f"{rel}:{number}: requires line must be the first line")


def check_inputs(root: Path) -> list[str]:
    errors: list[str] = []
    variants: dict[str, list] = {}
    seen: dict[tuple[str, str | None], str] = {}
    platform_repos = _check_csv(root, "platforms.csv", True, errors, variants, seen)
    package_repos = _check_csv(root, "packages.csv", False, errors, variants, seen)
    _check_bundles(root, variants, errors)

    nightly_path = root / "verified-nightly"
    if not nightly_path.is_file():
        errors.append("verified-nightly: file is missing")
    else:
        value = nightly_path.read_text(encoding="utf-8")
        if value.endswith("\n"):
            value = value[:-1]
        if not NIGHTLY_RE.match(value):
            errors.append(f"verified-nightly: {value!r} does not match {NIGHTLY_RE.pattern}")

    templates = root / "templates"
    generic = templates / "generic.roc"
    if not generic.is_file():
        errors.append("templates/generic.roc: file is missing")
    if templates.is_dir():
        versions: dict[tuple[str, str], list[str]] = {}
        for path in sorted(templates.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            parts = path.relative_to(templates).parts
            if parts == ("generic.roc",):
                _check_template(root, path, package_repos, errors)
            elif len(parts) == 3 and path.suffix == ".roc":
                repo = f"{parts[0]}/{parts[1]}"
                tag = path.stem
                if repo not in platform_repos:
                    errors.append(f'{rel}: platform "{repo}" is not in platforms.csv')
                if not TAG_RE.match(tag):
                    errors.append(f'{rel}: tag "{tag}" does not match {TAG_RE.pattern}')
                else:
                    version = tag[1:] if tag.startswith("v") else tag
                    versions.setdefault((repo, version), []).append(tag)
                _check_template(root, path, package_repos, errors)
            else:
                errors.append(f"{rel}: unexpected file in templates/")
        for (repo, _), tags in versions.items():
            if len(tags) > 1:
                named = " and ".join(f'"{t}"' for t in sorted(tags))
                errors.append(f"templates/{repo}: tags {named} name the same version")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    errors = check_inputs(root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
