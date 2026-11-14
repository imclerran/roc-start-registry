"""Validate the registry inputs: CSV lists, templates, and verified-nightly."""
import re
import sys
from pathlib import Path

ASSET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{44}\.tar\.zst$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+$")
TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
NIGHTLY_RE = re.compile(r"^nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]{7}$")
PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")
REQUIRES_RE = re.compile(
    r"^# requires: ([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)(, [A-Za-z0-9._-]+/[A-Za-z0-9._-]+)*$"
)

REQUIRES_PREFIX = "# requires:"


def _csv_rows_with_lines(path: Path) -> list[tuple[int, str, str]]:
    rows = []
    text = path.read_text(encoding="utf-8")
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line == "repo,alias":
            continue
        repo, _, alias = line.partition(",")
        rows.append((number, repo.strip(), alias.strip()))
    return rows


def read_csv_rows(path: Path) -> list[tuple[str, str]]:
    """Return (repo, alias) rows, skipping blanks, comments, and the header."""
    return [(repo, alias) for _, repo, alias in _csv_rows_with_lines(path)]


def parse_requires(text: str) -> list[str]:
    """Return repos named on a line-1 `# requires:` line, else []."""
    first = text.split("\n", 1)[0].rstrip("\r")
    if not REQUIRES_RE.match(first):
        return []
    return [r.strip() for r in first[len("# requires:"):].split(",")]


def _check_csv(root: Path, name: str, is_platform: bool, errors: list[str]) -> list[str]:
    path = root / name
    if not path.is_file():
        errors.append(f"{name}: file is missing")
        return []
    repos, aliases = {}, {}
    for number, repo, alias in _csv_rows_with_lines(path):
        if not REPO_RE.match(repo):
            errors.append(f'{name}:{number}: repo "{repo}" does not match {REPO_RE.pattern}')
        if not ALIAS_RE.match(alias):
            errors.append(f'{name}:{number}: alias "{alias}" does not match {ALIAS_RE.pattern}')
        if is_platform and alias != "pf":
            errors.append(f'{name}:{number}: platform alias "{alias}" must be "pf"')
        if repo in repos:
            errors.append(f'{name}:{number}: duplicate repo "{repo}" (first on line {repos[repo]})')
        else:
            repos[repo] = number
        if not is_platform and alias in aliases:
            errors.append(f'{name}:{number}: duplicate alias "{alias}" (first on line {aliases[alias]})')
        else:
            aliases.setdefault(alias, number)
    return list(repos)


def _check_template(root: Path, path: Path, packages: set[str], errors: list[str]) -> None:
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
    for number, line in enumerate(lines[1:], start=2):
        if line.startswith(REQUIRES_PREFIX):
            errors.append(f"{rel}:{number}: requires line must be the first line")


def check_inputs(root: Path) -> list[str]:
    errors: list[str] = []
    package_repos = set(_check_csv(root, "packages.csv", False, errors))
    platform_repos = set(_check_csv(root, "platforms.csv", True, errors))

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
                _check_template(root, path, package_repos, errors)
            else:
                errors.append(f"{rel}: unexpected file in templates/")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    errors = check_inputs(root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
