import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from scripts import generate
from scripts.check_inputs import ASSET_RE, read_csv_rows
from scripts.generate import (
    build_releases,
    build_templates_index,
    fetch_releases_github,
    FetchError,
    semver_key,
    write_outputs,
)

FIXTURE = Path(__file__).parent / "fixtures" / "basic_cli_releases.json"
MULTI_FIXTURE = Path(__file__).parent / "fixtures" / "multi_bundle_releases.json"
ASSET_A = "7brgwR8YUqfeGrkdfF1V4GPPN7DnP7ftqub9KcrTEZtr.tar.zst"

# The bundle hashes in multi_bundle_releases.json (repo x/ray), by letter.
H = {
    "A": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "B": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    "C": "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
    "D": "DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
    "E": "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
    "F": "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
    "G": "GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG",
    "H": "HHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHH",
    "I": "iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii",  # "I" is not in base58
    "J": "JJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJ",
    "K": "KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKK",
    "L": "LLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLL",  # types-0.9.0, a non-version tag
}
HASH_43 = "3Jq8sYkVbCwZ5rTnPd2HfLx9MgEu7aRo4ZiKe6WtNhB"  # basic-cli 0.27.0
ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ["default", "wayland"]
RAY_ROWS = [("pf", "default"), ("pf", "wayland")]


def fixture():
    return json.loads(FIXTURE.read_text())


def multi():
    return json.loads(MULTI_FIXTURE.read_text())


def by_tag(tag, releases=None):
    return next(r for r in (releases or fixture()) if r["tag_name"] == tag)


def ray(tag):
    return by_tag(tag, multi())


def asset_name(result):
    return {variant: asset["name"] for variant, asset in result.items()}


def quiet(_msg):
    pass


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class GenerateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def make_templates(self) -> Path:
        t = self.tmp / "templates_src"
        write(t / "generic.roc", "app [main!] {}\n{{dependencies}}\n")
        write(t / "README.txt", "ignore me")
        write(t / "roc-lang/basic-cli/0.28.0.roc", "{{dependencies}}\n")
        write(t / "roc-lang/basic-cli/0.26.0.roc", "{{dependencies}}\n")
        write(t / "a/b/v0.1.0.roc", "{{dependencies}}\n")
        write(t / "a/b/v0.0.6.roc", "{{dependencies}}\n")
        return t

    def test_asset_re_parses_prefix_and_hash(self):
        m = ASSET_RE.match(f"wayland-{H['A']}.tar.zst")
        self.assertEqual((m.group("variant"), m.group("hash")), ("wayland", H["A"]))
        m = ASSET_RE.match(f"{H['A']}.tar.zst")
        self.assertEqual((m.group("variant"), m.group("hash")), (None, H["A"]))
        self.assertIsNone(ASSET_RE.match(f"x-{H['A'][:42]}.tar.zst"))

    def identify(self, tag, variants, rows=None):
        logged = []
        result = generate.identify_bundles("x/ray", ray(tag), variants, rows or {}, log=logged.append)
        return result, logged

    def test_identify_single_bundle_no_variants(self):
        result, logged = self.identify("1.0.0", [])
        self.assertEqual(asset_name(result), {None: f"{H['G']}.tar.zst"})
        self.assertEqual(logged, [])

    def test_identify_multi_bundle_no_variants_unresolved(self):
        result, logged = self.identify("1.2.0", [])
        self.assertIsNone(result)
        self.assertEqual(len(logged), 1)
        self.assertIn("x/ray 1.2.0", logged[0])
        self.assertIn("several bundles but no variant rows", logged[0])

    def test_identify_single_bundle_with_variants_is_default(self):
        result, _ = self.identify("1.0.0", VARIANTS)
        self.assertEqual(asset_name(result), {"default": f"{H['G']}.tar.zst"})

    def lone(self, name):
        return {"tag_name": "2.0.0", "published_at": "2026-04-01T00:00:00Z",
                "assets": [{"name": name, "browser_download_url": f"https://example.invalid/{name}"}]}

    def test_identify_single_prefixed_bundle_uses_prefix(self):
        name = f"wayland-{H['B']}.tar.zst"
        result = generate.identify_bundles("x/ray", self.lone(name), VARIANTS, {}, log=quiet)
        self.assertEqual(asset_name(result), {"wayland": name})

    def test_identify_single_undeclared_prefix_unresolved(self):
        logged = []
        result = generate.identify_bundles("x/ray", self.lone(f"other-{H['H']}.tar.zst"), VARIANTS, {},
                                           log=logged.append)
        self.assertIsNone(result)
        self.assertEqual(logged, ["x/ray 2.0.0: unresolved — prefix 'other' is not a declared variant"])

    def test_identify_prefixed(self):
        result, logged = self.identify("1.3.0", VARIANTS)
        self.assertEqual(
            asset_name(result),
            {"default": f"default-{H['A']}.tar.zst", "wayland": f"wayland-{H['B']}.tar.zst"},
        )
        self.assertEqual(logged, [])

    def test_identify_undeclared_prefix_unresolved(self):
        result, logged = self.identify("0.9.0", VARIANTS)
        self.assertIsNone(result)
        self.assertEqual(logged, ["x/ray 0.9.0: unresolved — prefix 'other' is not a declared variant"])

    def test_identify_bare_via_bundle_rows(self):
        rows = {("x/ray", "1.2.0", H["C"]): "wayland", ("x/ray", "1.2.0", H["D"]): "default"}
        result, logged = self.identify("1.2.0", VARIANTS, rows)
        self.assertEqual(
            asset_name(result), {"wayland": f"{H['C']}.tar.zst", "default": f"{H['D']}.tar.zst"}
        )
        self.assertEqual(logged, [])

    def test_identify_one_bare_is_default(self):
        result, _ = self.identify("1.1.0", VARIANTS)
        self.assertEqual(
            asset_name(result), {"default": f"{H['E']}.tar.zst", "wayland": f"wayland-{H['F']}.tar.zst"}
        )

    def test_identify_two_bare_unresolved(self):
        result, logged = self.identify("1.2.0", VARIANTS)
        self.assertIsNone(result)
        self.assertEqual(logged, ["x/ray 1.2.0: unresolved — 2 bundles without prefix or bundles.csv row"])

    def test_identify_duplicate_variant_unresolved(self):
        result, logged = self.identify("0.8.0", VARIANTS)
        self.assertIsNone(result)
        self.assertEqual(logged, ["x/ray 0.8.0: unresolved — two bundles for variant 'default'"])

    def test_pick_43_char_bundle_listed(self):
        [entry], _ = generate.build_entries("roc-lang/basic-cli", [("pf", None)], fixture(), {}, log=quiet)
        [release] = [r for r in entry["releases"] if r["tag"] == "0.27.0"]
        self.assertTrue(release["url"].endswith(f"/0.27.0/{HASH_43}.tar.zst"))

    def test_non_matching_zst_asset_warns(self):
        release = {"tag_name": "1.0.0", "published_at": "2026-01-01T00:00:00Z", "assets": [
            {"name": f"{H['A']}.tar.zst", "browser_download_url": "u"},
            {"name": f"Wayland-{H['B']}.tar.zst", "browser_download_url": "w"},
            {"name": "docs.tar.gz", "browser_download_url": "d"},
        ]}
        logged = []
        [entry], unresolved = generate.build_entries("a/b", [("b", None)], [release], {}, log=logged.append)
        self.assertEqual(logged, [f"a/b 1.0.0: ignoring asset Wayland-{H['B']}.tar.zst: not a bundle filename"])
        self.assertEqual([r["url"] for r in entry["releases"]], ["u"])
        self.assertEqual(unresolved, [])

    def test_build_entries_skips_non_version_tags(self):
        logged = []
        entries, unresolved = generate.build_entries("x/ray", RAY_ROWS, multi(), {}, log=logged.append)
        self.assertEqual([r["tag"] for r in entries[0]["releases"]], ["1.3.0", "1.1.0", "1.0.0"])
        self.assertNotIn("types-0.9.0", [r["tag"] for e in entries for r in e["releases"]])
        self.assertIn("x/ray types-0.9.0: skipped, tag is not a version", logged)
        self.assertNotIn("types-0.9.0", [u["tag"] for u in unresolved])
        # A non-version release with no bundle asset at all is skipped silently.
        self.assertFalse([line for line in logged if "link-inputs" in line])
        self.assertNotIn("link-inputs-sha256-0123abcd", [u["tag"] for u in unresolved])

    def test_tracking_list_ignores_skipped_tags(self):
        gui = {"tag_name": "gui-0.1.0-rc.1", "published_at": "2026-05-01T00:00:00Z", "assets": [
            {"name": f"{H['C']}.tar.zst", "browser_download_url": "c"},
            {"name": f"{H['D']}.tar.zst", "browser_download_url": "d"},
        ]}
        releases = [gui] + [r for r in multi() if r["tag_name"] != "1.3.0"]
        entries, unresolved = generate.build_entries("x/ray", RAY_ROWS, releases, {}, log=quiet)
        self.assertEqual([u["tag"] for u in unresolved], ["1.2.0", "0.9.0", "0.8.0"])
        self.assertEqual(
            generate.tracking_list(unresolved, entries),
            ["x/ray 1.2.0: 2 bundles without prefix or bundles.csv row"],
        )

    def test_build_entries_one_entry_per_row_with_variant(self):
        entries, _ = generate.build_entries("x/ray", RAY_ROWS, multi(), {}, log=quiet)
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(set(entry), {"repo", "alias", "variant", "releases"})
        self.assertEqual([e["variant"] for e in entries], ["default", "wayland"])
        self.assertEqual([r["tag"] for r in entries[0]["releases"]], ["1.3.0", "1.1.0", "1.0.0"])
        self.assertEqual([r["tag"] for r in entries[1]["releases"]], ["1.3.0", "1.1.0"])
        self.assertTrue(entries[1]["releases"][0]["url"].endswith(f"/1.3.0/wayland-{H['B']}.tar.zst"))

    def test_build_entries_unresolved_list(self):
        logged = []
        _, unresolved = generate.build_entries("x/ray", RAY_ROWS, multi(), {}, log=logged.append)
        self.assertEqual(
            unresolved,
            [
                {"repo": "x/ray", "tag": "1.2.0", "published": "2026-02-01T00:00:00Z",
                 "reason": "2 bundles without prefix or bundles.csv row"},
                {"repo": "x/ray", "tag": "0.9.0", "published": "2025-11-01T00:00:00Z",
                 "reason": "prefix 'other' is not a declared variant"},
                {"repo": "x/ray", "tag": "0.8.0", "published": "2025-10-01T00:00:00Z",
                 "reason": "two bundles for variant 'default'"},
            ],
        )
        self.assertEqual(len(logged), 4)
        self.assertIn("x/ray types-0.9.0: skipped, tag is not a version", logged)

    def test_build_entries_no_variants_variant_is_null(self):
        entries, unresolved = generate.build_entries("roc-lang/basic-cli", [("pf", None)], fixture(), {}, log=quiet)
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]["variant"])
        self.assertEqual(len(entries[0]["releases"]), 4)
        self.assertEqual(unresolved, [])

    def test_tracking_list_only_newer_than_newest_resolved(self):
        entries, unresolved = generate.build_entries("x/ray", RAY_ROWS, multi(), {}, log=quiet)
        self.assertEqual(generate.tracking_list(unresolved, entries), [])
        releases = [r for r in multi() if r["tag_name"] != "1.3.0"]
        entries, unresolved = generate.build_entries("x/ray", RAY_ROWS, releases, {}, log=quiet)
        self.assertEqual(
            generate.tracking_list(unresolved, entries),
            ["x/ray 1.2.0: 2 bundles without prefix or bundles.csv row"],
        )

    def test_tracking_list_repo_with_nothing_resolved_lists_all(self):
        releases = [r for r in multi() if r["tag_name"] in ("1.2.0", "0.9.0", "0.8.0")]
        entries, unresolved = generate.build_entries("x/ray", RAY_ROWS, releases, {}, log=quiet)
        unresolved.append({"repo": "a/b", "tag": "2.0.0", "published": None, "reason": "r"})
        self.assertEqual(
            generate.tracking_list(unresolved, entries),
            [
                "a/b 2.0.0: r",
                "x/ray 0.8.0: two bundles for variant 'default'",
                "x/ray 0.9.0: prefix 'other' is not a declared variant",
                "x/ray 1.2.0: 2 bundles without prefix or bundles.csv row",
            ],
        )

    def test_build_releases_returns_doc_and_unresolved(self):
        calls = []

        def fetch(repo):
            calls.append(repo)
            return multi() if repo == "x/ray" else fixture()

        doc, unresolved, failed = build_releases(
            [("x/ray", "pf", "default"), ("x/ray", "pf", "wayland")],
            [("roc-lang/basic-cli", "cli", None)],
            fetch,
            "nightly-2026-11-10-abcdef0",
            "now",
            {},
            log=quiet,
        )
        self.assertEqual(calls, ["x/ray", "roc-lang/basic-cli"])
        self.assertEqual(list(doc), ["generated", "roc", "platforms", "packages"])
        self.assertEqual([(e["repo"], e["variant"]) for e in doc["platforms"]],
                         [("x/ray", "default"), ("x/ray", "wayland")])
        self.assertEqual(set(doc["packages"][0]), {"repo", "alias", "variant", "releases"})
        self.assertIsNone(doc["packages"][0]["variant"])
        self.assertIsInstance(unresolved, list)
        self.assertEqual([u["tag"] for u in unresolved], ["1.2.0", "0.9.0", "0.8.0"])
        self.assertEqual(failed, [])

    def test_build_entry_skips_drafts_keeps_prereleases(self):
        [entry], _ = generate.build_entries("roc-lang/basic-cli", [("cli", None)], fixture(), {}, log=quiet)
        self.assertEqual([r["tag"] for r in entry["releases"]], ["0.28.1", "0.28.0", "0.27.0", "0.26.0-rc3"])

    def test_build_entry_newest_first_and_shape(self):
        shuffled = list(reversed(fixture()))
        [entry], _ = generate.build_entries("roc-lang/basic-cli", [("cli", None)], shuffled, {}, log=quiet)
        self.assertEqual(set(entry), {"repo", "alias", "variant", "releases"})
        for release in entry["releases"]:
            self.assertEqual(set(release), {"tag", "url", "published"})
        published = [r["published"] for r in entry["releases"]]
        self.assertEqual(published, sorted(published, reverse=True))
        self.assertEqual(entry["releases"][0]["published"], "2026-11-13T22:43:38Z")
        self.assertTrue(entry["releases"][0]["url"].endswith(ASSET_A))

    def test_build_entry_no_qualifying_releases_is_empty_list(self):
        [entry], _ = generate.build_entries("a/b", [("b", None)], [by_tag("0.19.0"), by_tag("9.9.9-draft")], {},
                                            log=quiet)
        self.assertEqual(entry["releases"], [])

    def test_semver_key_orders_v_prefix_and_prerelease(self):
        tags = ["v1.0.0", "0.9.0", "1.0.0-rc1"]
        self.assertEqual(sorted(tags, key=semver_key), ["0.9.0", "1.0.0-rc1", "v1.0.0"])

    def test_semver_key_prerelease_numeric_identifiers(self):
        self.assertLess(semver_key("1.0.0-rc.2"), semver_key("1.0.0-rc.10"))
        tags = ["1.0.0", "1.0.0-rc.10", "1.0.0-beta", "1.0.0-rc.2", "1.0.0-1", "v0.7", "0.7.1", "1.0.0-rc"]
        self.assertEqual(sorted(tags, key=semver_key),
                         ["v0.7", "0.7.1", "1.0.0-1", "1.0.0-beta", "1.0.0-rc", "1.0.0-rc.2", "1.0.0-rc.10", "1.0.0"])

    def test_build_releases_shape_and_nightly(self):
        result, _, _ = build_releases(
            [("roc-lang/basic-cli", "pf", None)],
            [("a/b", "b", None)],
            lambda repo: fixture(),
            "nightly-2026-11-10-abcdef0",
            "2026-11-14T00:00:00Z",
            {},
            log=quiet,
        )
        self.assertEqual(list(result), ["generated", "roc", "platforms", "packages"])
        self.assertEqual(result["roc"], {"nightly": "nightly-2026-11-10-abcdef0"})
        self.assertEqual(result["generated"], "2026-11-14T00:00:00Z")
        self.assertEqual(result["platforms"][0]["repo"], "roc-lang/basic-cli")
        self.assertEqual(result["packages"][0]["alias"], "b")

    def failing_build(self, previous):
        def fetch(repo):
            if repo == "bad/repo":
                raise RuntimeError("boom")
            return fixture()

        logged = []
        result = build_releases(
            [("roc-lang/basic-cli", "pf", None)],
            [("bad/repo", "bad", "one"), ("bad/repo", "bad2", "two"), ("a/b", "b", None)],
            fetch,
            "nightly-2026-11-10-abcdef0",
            "now",
            {},
            previous,
            log=logged.append,
        )
        return result, [line for line in logged if line.startswith("bad/repo")]

    def test_build_releases_fetch_failure_without_previous_is_empty(self):
        (result, _, failed), logged = self.failing_build(None)
        self.assertEqual(logged, ["bad/repo: fetch failed (boom); no previous releases"])
        self.assertEqual(failed, ["bad/repo"])
        self.assertEqual(
            [(e["repo"], e["variant"], e["releases"]) for e in result["packages"][:2]],
            [("bad/repo", "one", []), ("bad/repo", "two", [])],
        )
        self.assertEqual(len(result["packages"][2]["releases"]), 4)
        self.assertEqual(len(result["platforms"][0]["releases"]), 4)

    def test_build_releases_fetch_failure_carries_over_previous(self):
        kept = [{"tag": "0.1.0", "url": "u-one", "published": "2026-01-01T00:00:00Z"}]
        previous = {
            "generated": "earlier", "roc": {"nightly": "n"},
            "platforms": [{"repo": "bad/repo", "alias": "pf", "variant": "two", "releases": [{"tag": "x"}]}],
            "packages": [
                {"repo": "bad/repo", "alias": "bad", "variant": "one", "releases": kept},
                {"repo": "a/b", "alias": "b", "variant": None, "releases": [{"tag": "stale"}]},
            ],
        }
        (result, unresolved, failed), logged = self.failing_build(previous)
        self.assertEqual(logged, ["bad/repo: fetch failed (boom); keeping previous releases"])
        self.assertEqual(failed, ["bad/repo"])
        # Each (repo, variant) takes its own previous releases; "two" was a platform entry before, so it
        # still matches by (repo, variant), and a repo that fetched fine ignores the previous file.
        self.assertEqual(
            [(e["variant"], e["releases"]) for e in result["packages"]],
            [("one", kept), ("two", [{"tag": "x"}]), (None, result["packages"][2]["releases"])],
        )
        self.assertEqual(len(result["packages"][2]["releases"]), 4)
        self.assertEqual(unresolved, [])

    def test_main_all_fetches_fail_writes_nothing_and_returns_2(self):
        out, unresolved = self.tmp / "out", self.tmp / "unresolved.txt"
        calls = []

        def fetch(repo):
            calls.append(repo)
            raise RuntimeError("outage")

        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = generate.main(["--out", str(out), "--root", str(ROOT), "--unresolved", str(unresolved),
                                  "--token", "x"], fetch=fetch)
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())
        self.assertFalse(unresolved.exists())
        self.assertGreater(len(calls), 1)
        self.assertIn("error: every fetch failed; nothing written", err.getvalue())

    def test_main_partial_failure_keeps_previous_from_out_dir(self):
        out = self.tmp / "out"
        kept = [{"tag": "0.28.0", "url": "u", "published": "2026-11-11T06:26:08Z"}]
        write(out / "releases.json", json.dumps(
            {"generated": "g", "roc": {"nightly": "n"},
             "platforms": [{"repo": "roc-lang/basic-cli", "alias": "pf", "variant": None, "releases": kept}],
             "packages": []}))

        def fetch(repo):
            if repo == "roc-lang/basic-cli":
                raise RuntimeError("boom")
            return []

        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = generate.main(["--out", str(out), "--root", str(ROOT), "--token", "x"], fetch=fetch)
        self.assertEqual(code, 0)
        doc = json.loads((out / "releases.json").read_text())
        [cli] = [e for e in doc["platforms"] if e["repo"] == "roc-lang/basic-cli"]
        self.assertEqual(cli["releases"], kept)

    def test_build_releases_repo_in_both_files(self):
        release = {
            "tag_name": "1.0.0", "draft": False, "published_at": "2026-01-01T00:00:00Z",
            "assets": [
                {"name": f"{H['A']}.tar.zst", "browser_download_url": "u-platform"},
                {"name": f"tool-{H['B']}.tar.zst", "browser_download_url": "u-tool"},
            ],
        }
        calls = []

        def fetch(repo):
            calls.append(repo)
            return [release]

        doc, unresolved, _ = build_releases(
            [("a/b", "pf", "default")], [("a/b", "tool", "tool")], fetch, "n", "now", {}, log=quiet
        )
        self.assertEqual(calls, ["a/b"])
        self.assertEqual(
            doc["platforms"],
            [{"repo": "a/b", "alias": "pf", "variant": "default",
              "releases": [{"tag": "1.0.0", "url": "u-platform", "published": "2026-01-01T00:00:00Z"}]}],
        )
        self.assertEqual(
            doc["packages"],
            [{"repo": "a/b", "alias": "tool", "variant": "tool",
              "releases": [{"tag": "1.0.0", "url": "u-tool", "published": "2026-01-01T00:00:00Z"}]}],
        )
        self.assertEqual(unresolved, [])

    def test_read_rows_tolerates_crlf_header_blank(self):
        path = write(self.tmp / "p.csv", "repo,alias\r\n\r\n# comment\r\na/b,b\r\nc/d, d \r\n")
        self.assertEqual(read_csv_rows(path), [("a/b", "b", None), ("c/d", "d", None)])

    def test_build_templates_index_shape_and_sorted(self):
        index = build_templates_index(self.make_templates())
        self.assertEqual(
            index,
            {
                "generic": "templates/generic.roc",
                "templates": {
                    "roc-lang/basic-cli": [
                        {"tag": "0.26.0", "requires": []},
                        {"tag": "0.28.0", "requires": []},
                    ],
                    "a/b": [
                        {"tag": "v0.0.6", "requires": []},
                        {"tag": "v0.1.0", "requires": []},
                    ],
                },
            },
        )

    def test_build_templates_index_includes_requires(self):
        t = self.tmp / "t"
        write(t / "generic.roc", "{{dependencies}}\n")
        write(t / "x/y/1.0.0.roc", "# requires: roc-lang/http, a/b\n{{dependencies}}\n")
        index = build_templates_index(t)
        self.assertEqual(
            index["templates"]["x/y"],
            [{"tag": "1.0.0", "requires": ["roc-lang/http", "a/b"]}],
        )

    def sample_releases(self, generated="g1", nightly="nightly-2026-11-10-abcdef0"):
        return {
            "generated": generated,
            "roc": {"nightly": nightly},
            "platforms": [],
            "packages": [],
        }

    def test_write_outputs_writes_files_and_copies_templates(self):
        templates = self.make_templates()
        out = self.tmp / "out"
        write(out / "templates/stale.roc", "old")
        index = build_templates_index(templates)
        changed = write_outputs(out, self.sample_releases(), index, templates)
        self.assertTrue(changed)
        self.assertEqual(json.loads((out / "releases.json").read_text())["generated"], "g1")
        self.assertEqual(json.loads((out / "templates.json").read_text()), index)
        self.assertTrue((out / ".nojekyll").is_file())
        self.assertEqual((out / ".nojekyll").read_text(), "")
        self.assertTrue((out / "templates/generic.roc").is_file())
        self.assertTrue((out / "templates/a/b/v0.1.0.roc").is_file())
        self.assertFalse((out / "templates/stale.roc").exists())

    def test_write_outputs_unchanged_keeps_generated_and_returns_false(self):
        templates = self.make_templates()
        out = self.tmp / "out"
        index = build_templates_index(templates)
        write_outputs(out, self.sample_releases("first"), index, templates)
        changed = write_outputs(out, self.sample_releases("second"), index, templates)
        self.assertFalse(changed)
        self.assertEqual(json.loads((out / "releases.json").read_text())["generated"], "first")

    def test_write_outputs_changed_returns_true(self):
        templates = self.make_templates()
        out = self.tmp / "out"
        index = build_templates_index(templates)
        write_outputs(out, self.sample_releases("first"), index, templates)
        changed = write_outputs(
            out, self.sample_releases("second", nightly="nightly-2026-11-12-1234567"), index, templates
        )
        self.assertTrue(changed)
        data = json.loads((out / "releases.json").read_text())
        self.assertEqual(data["generated"], "second")
        # template-only change also counts
        write(templates / "a/b/v0.2.0.roc", "{{dependencies}}\n")
        self.assertTrue(write_outputs(out, data, build_templates_index(templates), templates))

    def test_fetch_paginates_via_link_header(self):
        class Response:
            def __init__(self, body, link=None):
                self.body, self.link = body, link
                self.headers = self

            def get(self, name, default=None):
                return self.link if name == "Link" else default

            def read(self):
                return json.dumps(self.body).encode()

        next_url = "https://api.github.com/repositories/1/releases?per_page=100&page=2"
        pages = [
            Response([{"tag_name": "a"}], f'<{next_url}>; rel="next", <https://x/last>; rel="last"'),
            Response([{"tag_name": "b"}]),
        ]
        seen = []

        def opener(request, timeout=None):
            seen.append((request, timeout))
            return pages[len(seen) - 1]

        result = fetch_releases_github("o/r", "tok", opener)
        self.assertEqual([r["tag_name"] for r in result], ["a", "b"])
        self.assertEqual(seen[0][0].full_url, "https://api.github.com/repos/o/r/releases?per_page=100")
        self.assertEqual(seen[1][0].full_url, next_url)
        self.assertEqual(seen[0][0].get_header("Authorization"), "Bearer tok")
        self.assertEqual([timeout for _, timeout in seen], [30, 30])

    def test_fetch_http_error_raises_fetch_error(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        with self.assertRaises(FetchError) as ctx:
            fetch_releases_github("o/missing", None, opener)
        self.assertIn("o/missing", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
