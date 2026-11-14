import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_inputs import read_csv_rows
from scripts.generate import (
    build_entry,
    build_releases,
    build_templates_index,
    pick_asset,
    semver_key,
    write_outputs,
)

FIXTURE = Path(__file__).parent / "fixtures" / "basic_cli_releases.json"
ASSET_A = "7brgwR8YUqfeGrkdfF1V4GPPN7DnP7ftqub9KcrTEZtr.tar.zst"
ASSET_B = "2yhxoR7cZPSYbyUzPWULFw8AA6QEcdsgFNRmzu7pZbst.tar.zst"


def fixture():
    return json.loads(FIXTURE.read_text())


def by_tag(tag):
    return next(r for r in fixture() if r["tag_name"] == tag)


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

    def test_pick_asset_returns_matching_zst(self):
        asset = pick_asset(by_tag("0.28.1"))
        self.assertEqual(asset["name"], ASSET_A)

    def test_pick_asset_ignores_legacy_br_and_tar_gz(self):
        self.assertIsNone(pick_asset(by_tag("0.19.0")))
        self.assertIsNone(pick_asset(by_tag("linker-inputs-548c70b9")))

    def test_pick_asset_first_of_two_warns(self):
        release = {
            "tag_name": "1.0.0",
            "assets": [
                {"name": ASSET_A, "browser_download_url": "u1"},
                {"name": ASSET_B, "browser_download_url": "u2"},
            ],
        }
        logged = []
        asset = pick_asset(release, log=logged.append)
        self.assertEqual(asset["browser_download_url"], "u1")
        self.assertEqual(len(logged), 1)
        self.assertIn("1.0.0", logged[0])

    def test_build_entry_skips_drafts_keeps_prereleases(self):
        entry = build_entry("roc-lang/basic-cli", "cli", fixture(), log=lambda m: None)
        self.assertEqual([r["tag"] for r in entry["releases"]], ["0.28.1", "0.28.0", "0.26.0-rc3"])

    def test_build_entry_newest_first_and_shape(self):
        shuffled = list(reversed(fixture()))
        entry = build_entry("roc-lang/basic-cli", "cli", shuffled, log=lambda m: None)
        self.assertEqual(set(entry), {"repo", "alias", "releases"})
        for release in entry["releases"]:
            self.assertEqual(set(release), {"tag", "url", "published"})
        published = [r["published"] for r in entry["releases"]]
        self.assertEqual(published, sorted(published, reverse=True))
        self.assertEqual(entry["releases"][0]["published"], "2026-11-13T22:43:38Z")
        self.assertTrue(entry["releases"][0]["url"].endswith(ASSET_A))

    def test_build_entry_no_qualifying_releases_is_empty_list(self):
        entry = build_entry("a/b", "b", [by_tag("0.19.0"), by_tag("9.9.9-draft")], log=lambda m: None)
        self.assertEqual(entry["releases"], [])

    def test_semver_key_orders_v_prefix_and_prerelease(self):
        tags = ["v1.0.0", "0.9.0", "1.0.0-rc1"]
        self.assertEqual(sorted(tags, key=semver_key), ["0.9.0", "1.0.0-rc1", "v1.0.0"])

    def test_build_releases_shape_and_nightly(self):
        result = build_releases(
            [("roc-lang/basic-cli", "pf")],
            [("a/b", "b")],
            lambda repo: fixture(),
            "nightly-2026-11-10-abcdef0",
            "2026-11-14T00:00:00Z",
            log=lambda m: None,
        )
        self.assertEqual(list(result), ["generated", "roc", "platforms", "packages"])
        self.assertEqual(result["roc"], {"nightly": "nightly-2026-11-10-abcdef0"})
        self.assertEqual(result["generated"], "2026-11-14T00:00:00Z")
        self.assertEqual(result["platforms"][0]["repo"], "roc-lang/basic-cli")
        self.assertEqual(result["packages"][0]["alias"], "b")

    def test_build_releases_fetch_failure_gives_empty_entry(self):
        def fetch(repo):
            if repo == "bad/repo":
                raise RuntimeError("boom")
            return fixture()

        logged = []
        result = build_releases(
            [("roc-lang/basic-cli", "pf")],
            [("bad/repo", "bad"), ("a/b", "b")],
            fetch,
            "nightly-2026-11-10-abcdef0",
            "now",
            log=logged.append,
        )
        self.assertEqual(len(logged), 1)
        self.assertIn("bad/repo", logged[0])
        self.assertEqual(result["packages"][0]["repo"], "bad/repo")
        self.assertEqual(result["packages"][0]["releases"], [])
        self.assertEqual(len(result["packages"][1]["releases"]), 3)
        self.assertEqual(len(result["platforms"][0]["releases"]), 3)

    def test_read_rows_tolerates_crlf_header_blank(self):
        path = write(self.tmp / "p.csv", "repo,alias\r\n\r\n# comment\r\na/b,b\r\nc/d, d \r\n")
        self.assertEqual(read_csv_rows(path), [("a/b", "b"), ("c/d", "d")])

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


if __name__ == "__main__":
    unittest.main()
