import tempfile
import unittest
from pathlib import Path

from scripts import check_inputs as ci
from scripts.check_inputs import check_inputs, read_csv_rows

GENERIC = "app [main!] {{dependencies}}\n"
PF_TEMPLATE = "app [main!] {{dependencies}}\nimport {{pf}}.Stdout\n"
PKGS = "repo,alias\nroc-lang/http,http\n"
PFS = "repo,alias\nroc-lang/basic-cli,pf\n"
HASH = "A8Dt5CorYsEtHFh8h7SfMtpzSy73UWbaB5Twy9JoYS1U"
VARIANT_PKGS = "repo,alias,variant\nroc-lang/http,http\na/b,ab,default\na/b,ab2,wayland\n"


def make_root(tmp, packages=PKGS, platforms=PFS, templates=None,
              nightly="nightly-2026-11-12-0123abc", bundles=None):
    root = Path(tmp)
    if bundles is not None:
        (root / "bundles.csv").write_text(bundles, newline="")
    (root / "packages.csv").write_text(packages, newline="")
    (root / "platforms.csv").write_text(platforms, newline="")
    (root / "verified-nightly").write_text(nightly + "\n")
    if templates is None:
        templates = {
            "generic.roc": GENERIC,
            "roc-lang/basic-cli/0.26.0.roc": PF_TEMPLATE,
        }
    for rel, text in templates.items():
        p = root / "templates" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


class CheckInputsTests(unittest.TestCase):
    def check(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            return check_inputs(make_root(tmp, **kwargs))

    def tpl(self, rel, text):
        return {"generic.roc": GENERIC, rel: text}

    def test_valid_root_has_no_errors(self):
        self.assertEqual(self.check(), [])

    def test_csv_crlf_and_whitespace_ok(self):
        errors = self.check(packages="repo,alias\r\n a/b , ab \r\n\r\n")
        self.assertEqual(errors, [])

    def test_bad_repo_path_rejected(self):
        errors = self.check(packages="repo,alias\nnota repo,x\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("packages.csv:2:", errors[0])

    def test_bad_alias_rejected(self):
        errors = self.check(packages="repo,alias\na/b,Bad-Alias\n")
        self.assertEqual(len(errors), 1)
        self.assertIn('packages.csv:2: alias "Bad-Alias"', errors[0])

    def test_duplicate_repo_rejected(self):
        errors = self.check(packages="repo,alias\na/b,x\na/b,y\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("packages.csv:3:", errors[0])
        self.assertIn("a/b", errors[0])

    def test_duplicate_alias_rejected(self):
        errors = self.check(packages="repo,alias\na/b,x\na/c,x\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("packages.csv:3:", errors[0])

    def test_platform_alias_must_be_pf(self):
        errors = self.check(platforms="repo,alias\nroc-lang/basic-cli,cli\n",
                            templates={"generic.roc": GENERIC})
        self.assertEqual(len(errors), 1)
        self.assertIn("platforms.csv:2:", errors[0])

    def test_template_missing_dependencies_rejected(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/0.26.0.roc", "app [main!] {}\n"))
        self.assertEqual(len(errors), 1)
        self.assertIn("{{dependencies}}", errors[0])

    def test_template_two_dependencies_rejected(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/0.26.0.roc",
            "app [main!] {{dependencies}}\n{{dependencies}}\n"))
        self.assertEqual(len(errors), 1)
        self.assertIn("{{dependencies}}", errors[0])

    def test_template_unknown_placeholder_rejected(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/0.26.0.roc",
            "app [main!] {{dependencies}}\n{{platform}}\n"))
        self.assertEqual(len(errors), 1)
        self.assertIn("{{platform}}", errors[0])

    def test_template_multiple_pf_ok(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/0.26.0.roc",
            "app [main!] {{dependencies}}\n{{pf}}.A\n{{pf}}.B\n"))
        self.assertEqual(errors, [])

    def test_template_for_unlisted_platform_rejected(self):
        errors = self.check(templates=self.tpl("x/y/1.0.0.roc", GENERIC))
        self.assertEqual(len(errors), 1)
        self.assertIn("x/y", errors[0])

    def test_template_bad_tag_filename_rejected(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/latest.roc", GENERIC))
        self.assertEqual(len(errors), 1)
        self.assertIn("latest", errors[0])

    def test_template_v_prefixed_tag_ok(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/v0.0.6.roc", GENERIC))
        self.assertEqual(errors, [])

    def test_generic_template_required(self):
        errors = self.check(templates={"roc-lang/basic-cli/0.26.0.roc": PF_TEMPLATE})
        self.assertEqual(len(errors), 1)
        self.assertIn("generic.roc", errors[0])

    def test_nightly_format_enforced(self):
        errors = self.check(nightly="11/12")
        self.assertEqual(len(errors), 1)
        self.assertIn("verified-nightly", errors[0])

    def test_template_requires_listed_package_ok(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/0.26.0.roc",
            "# requires: roc-lang/http\n" + PF_TEMPLATE))
        self.assertEqual(errors, [])

    def test_template_requires_unlisted_package_rejected(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/0.26.0.roc",
            "# requires: a/missing\n" + PF_TEMPLATE))
        self.assertEqual(len(errors), 1)
        self.assertIn("a/missing", errors[0])

    def test_template_requires_not_first_line_rejected(self):
        errors = self.check(templates=self.tpl(
            "roc-lang/basic-cli/0.26.0.roc",
            "app [main!] {{dependencies}}\n# requires: roc-lang/http\n"))
        self.assertEqual(len(errors), 1)
        self.assertIn(":2:", errors[0])

    def test_variant_rows_ok(self):
        self.assertEqual(self.check(packages="a/b,ab,default\na/b,ab2,wayland\n"), [])

    def test_variant_bad_name_rejected(self):
        errors = self.check(packages="a/b,ab,Wayland-X\n")
        self.assertEqual(len(errors), 1)
        self.assertIn('packages.csv:1: variant "Wayland-X"', errors[0])

    def test_repo_with_and_without_variant_rejected(self):
        errors = self.check(packages="a/b,ab\na/b,ab2,x\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("packages.csv:2:", errors[0])
        self.assertIn("a/b", errors[0])

    def test_duplicate_repo_variant_rejected(self):
        errors = self.check(packages="a/b,ab,x\na/b,ab2,x\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("packages.csv:2:", errors[0])
        self.assertEqual(self.check(packages="a/b,ab,x\na/b,ab2,y\n"), [])

    def test_bundles_row_shape_rejected(self):
        errors = self.check(packages=VARIANT_PKGS, bundles="a/b,1.0.0,short,default\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("bundles.csv:1:", errors[0])
        self.assertIn("short", errors[0])

    def test_bundles_repo_without_variants_rejected(self):
        errors = self.check(bundles=f"roc-lang/http,1.0.0,{HASH},default\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("bundles.csv:1:", errors[0])
        self.assertIn("roc-lang/http", errors[0])

    def test_bundles_undeclared_variant_rejected(self):
        errors = self.check(packages=VARIANT_PKGS, bundles=f"a/b,1.0.0,{HASH},gui\n")
        self.assertEqual(len(errors), 1)
        self.assertIn('variant "gui"', errors[0])

    def test_bundles_duplicate_rejected(self):
        row = f"a/b,1.0.0,{HASH},default\n"
        errors = self.check(packages=VARIANT_PKGS, bundles="repo,tag,hash,variant\n" + row + row)
        self.assertEqual(len(errors), 1)
        self.assertIn("bundles.csv:3:", errors[0])

    def test_bundles_missing_file_ok(self):
        self.assertEqual(self.check(packages=VARIANT_PKGS), [])

    def test_read_csv_rows_returns_three_tuples(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.csv"
            path.write_text("repo,alias,variant\na/b,ab\nc/d, cd , x \n", newline="")
            self.assertEqual(read_csv_rows(path), [("a/b", "ab", None), ("c/d", "cd", "x")])

    def test_read_bundle_rows_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundles.csv"
            path.write_text(f"# repo,tag,hash,variant\r\nrepo,tag,hash,variant\r\n\r\na/b,1.0.0,{HASH},default\r\n",
                            newline="")
            self.assertEqual(ci.read_bundle_rows(path), {("a/b", "1.0.0", HASH): "default"})
            self.assertEqual(ci.read_bundle_rows(Path(tmp) / "missing.csv"), {})


if __name__ == "__main__":
    unittest.main()
