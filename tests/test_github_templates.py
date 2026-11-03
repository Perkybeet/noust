# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a person finds on GitHub when they report a problem or send a change.

The forms, the labels they apply, the pull request template, the code of conduct and the
agreement a first contribution signs are files, and a file that drifts from what the rest of the
project says is a report nobody can triage: a form that applies a label the repository does not
have applies nothing, and a link in the console that names a field the form lost opens an empty
box. These tests read the files as data and hold them to each other.

``CONSOLE_REPORT_*`` below is the contract with the console's "Report a problem": it opens
``issues/new?template=console_report.yml`` with the fields in ``CONSOLE_REPORT_PREFILLED`` as
query parameters (GitHub takes a field's ``id`` as the parameter's name, and a dropdown's value
must be one of its options, written exactly). Changing an id or an option is changing that
contract, so it is changed here first.

Nothing here talks to GitHub: the repository's own settings (Discussions, the ruleset of
``main``, private vulnerability reporting) are not in the tree.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
GITHUB = ROOT / ".github"
FORMS = GITHUB / "ISSUE_TEMPLATE"
WORKFLOW = GITHUB / "workflows" / "cla.yml"
REPOSITORY = "https://github.com/Perkybeet/noust"

#: The labels, by what they say. ``.github/labels.yml`` defines exactly these.
TYPE_LABELS = ["bug", "enhancement", "question", "duplicate", "wontfix"]
TRIAGE_LABELS = ["needs-triage", "needs-info", "confirmed"]
CONTRIBUTION_LABELS = ["from-console", "good first issue", "help wanted"]
AREAS = [
    "apps",
    "databases",
    "domains",
    "network",
    "server",
    "security",
    "backups",
    "console",
    "fleet",
    "cli",
    "docs",
    "packaging",
    "recipes",
]
AREA_LABELS = [f"area/{name}" for name in AREAS]

#: The ``area`` dropdown of the bug report, the console report and the feature request: each
#: option, written as the console must send it, and the label the maintainer gives it in triage.
AREA_OPTIONS: dict[str, str] = {
    "Applications and deploys": "area/apps",
    "Databases": "area/databases",
    "Domains and certificates": "area/domains",
    "Network, DNS and the web server": "area/network",
    "The server (services, monitoring, software, updates)": "area/server",
    "Security, accounts and access": "area/security",
    "Backups and restore": "area/backups",
    "Console (the browser)": "area/console",
    "Fleet (a central and its servers)": "area/fleet",
    "Command line": "area/cli",
    "Documentation": "area/docs",
    "Installing or updating Noust": "area/packaging",
    "Recipes (the catalogue)": "area/recipes",
}
OTHER_AREA = "Other"

#: What the console opens, and the parameters it fills in.
CONSOLE_REPORT_TEMPLATE = "console_report.yml"
CONSOLE_REPORT_FIELD_IDS = ["version", "area", "what-happened", "reproduce", "attachment"]
CONSOLE_REPORT_PREFILLED = ["title", "version", "area", "what-happened", "reproduce"]

#: Every form and the ids of its fields, in order. Links in the documentation and the console
#: rely on them, so they never change by accident.
FORM_FIELD_IDS: dict[str, list[str]] = {
    "bug_report.yml": [
        "version",
        "install",
        "platform",
        "area",
        "what-happened",
        "reproduce",
        "support-report",
    ],
    CONSOLE_REPORT_TEMPLATE: CONSOLE_REPORT_FIELD_IDS,
    "feature_request.yml": ["job", "proposal", "area", "version"],
    "recipe_request.yml": [
        "project",
        "stars",
        "license",
        "instructions",
        "why",
        "needs",
        "pinning",
        "help",
        "version",
    ],
}
FORMS_WITH_AN_AREA = ["bug_report.yml", CONSOLE_REPORT_TEMPLATE, "feature_request.yml"]

#: The words the CLA bot reads as a signature, as the agreement tells a contributor to write them.
SIGNATURE = "I have read the CLA Document and I hereby sign the CLA"
SIGNATURES_BRANCH = "cla-signatures"
CLA_ALLOWLIST = [
    "Perkybeet",
    "dependabot[bot]",
    "dependabot",
    "github-actions[bot]",
    "github-actions",
]

#: What a job that writes signatures to a branch and comments on a pull request has to hold.
CLA_JOB_PERMISSIONS = {
    "actions": "write",
    "contents": "write",
    "pull-requests": "write",
    "statuses": "write",
}

SHA = re.compile(r"[0-9a-f]{40}")
FIELD_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
COLOUR = re.compile(r"[0-9a-f]{6}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load(path: Path) -> Any:
    return yaml.safe_load(read(path))


def form(name: str) -> dict[str, Any]:
    data = load(FORMS / name)
    assert isinstance(data, dict)
    return data


def fields(name: str) -> dict[str, dict[str, Any]]:
    """The fields of one form by id; the explanatory text, which has none, is left out."""
    return {element["id"]: element for element in form(name)["body"] if "id" in element}


def defined_labels() -> dict[str, dict[str, str]]:
    return {entry["name"]: entry for entry in load(GITHUB / "labels.yml")}


def checkboxes(text: str) -> list[str]:
    return [line for line in text.splitlines() if re.match(r"- \[ \] ", line)]


class TestTheLabels:
    def test_the_file_defines_the_type_triage_contribution_and_area_labels_and_no_other(
        self,
    ) -> None:
        names = list(defined_labels())

        assert sorted(names) == sorted(
            TYPE_LABELS + TRIAGE_LABELS + CONTRIBUTION_LABELS + AREA_LABELS
        )
        assert len(names) == len(set(names))

    def test_each_has_a_hex_colour_and_a_short_description_in_english(self) -> None:
        for name, entry in defined_labels().items():
            assert COLOUR.fullmatch(entry["color"]), name
            description = entry["description"]
            # GitHub keeps 100 characters of a description and cuts the rest.
            assert 10 <= len(description) <= 100, name
            assert description.isascii() and description == description.strip(), name

    def test_the_areas_read_as_a_group_in_one_colour(self) -> None:
        labels = defined_labels()

        assert len({labels[name]["color"] for name in AREA_LABELS}) == 1

    def test_every_area_option_of_the_forms_names_a_defined_label(self) -> None:
        assert set(AREA_OPTIONS.values()) == set(AREA_LABELS)
        assert set(AREA_OPTIONS.values()) <= set(defined_labels())


class TestEveryForm:
    def test_no_form_is_missing_from_the_table_of_ids_and_none_is_listed_twice(self) -> None:
        on_disk = {path.name for path in FORMS.glob("*.yml")} - {"config.yml"}

        assert on_disk == set(FORM_FIELD_IDS)

    @pytest.mark.parametrize("name", sorted(FORM_FIELD_IDS))
    def test_it_has_a_name_a_description_and_a_body(self, name: str) -> None:
        data = form(name)

        assert data["name"] and data["description"]
        assert isinstance(data["body"], list) and data["body"]

    @pytest.mark.parametrize("name", sorted(FORM_FIELD_IDS))
    def test_its_fields_keep_their_ids_and_the_ids_are_written_the_way_a_link_needs(
        self, name: str
    ) -> None:
        ids = [element["id"] for element in form(name)["body"] if "id" in element]

        assert ids == FORM_FIELD_IDS[name]
        for field_id in ids:
            assert FIELD_ID.fullmatch(field_id), field_id

    @pytest.mark.parametrize("name", sorted(FORM_FIELD_IDS))
    def test_it_applies_labels_and_every_one_of_them_exists(self, name: str) -> None:
        labels = form(name).get("labels")
        assert labels, f"{name} applies no label, so nothing marks what the issue is"

        assert set(labels) <= set(defined_labels())
        assert "needs-triage" in labels
        assert len(set(labels) & set(TYPE_LABELS)) == 1

    @pytest.mark.parametrize("name", sorted(FORM_FIELD_IDS))
    def test_only_the_console_report_carries_the_label_of_the_console(self, name: str) -> None:
        carries = "from-console" in form(name)["labels"]

        assert carries == (name == CONSOLE_REPORT_TEMPLATE)

    @pytest.mark.parametrize("name", sorted(FORM_FIELD_IDS))
    def test_a_dropdown_offers_distinct_options(self, name: str) -> None:
        for field_id, element in fields(name).items():
            if element["type"] == "dropdown":
                options = element["attributes"]["options"]
                assert len(options) == len(set(options)), f"{name}: {field_id}"

    @pytest.mark.parametrize("name", FORMS_WITH_AN_AREA)
    def test_the_area_is_one_dropdown_of_the_same_options_in_every_form(self, name: str) -> None:
        area = fields(name)["area"]

        assert area["type"] == "dropdown"
        assert area["attributes"]["options"] == [*AREA_OPTIONS, OTHER_AREA]
        assert area["validations"]["required"] is True

    def test_the_recipe_request_names_its_area_by_label(self) -> None:
        assert "area/recipes" in form("recipe_request.yml")["labels"]

    @pytest.mark.parametrize("name", ["bug_report.yml", CONSOLE_REPORT_TEMPLATE])
    def test_a_bug_form_asks_for_the_version_and_what_happened(self, name: str) -> None:
        for field_id in ("version", "what-happened"):
            assert fields(name)[field_id]["validations"]["required"] is True, field_id


class TestTheConsoleReport:
    def test_it_applies_the_label_of_the_console_and_the_type_of_a_bug(self) -> None:
        assert set(form(CONSOLE_REPORT_TEMPLATE)["labels"]) == {
            "bug",
            "from-console",
            "needs-triage",
        }

    def test_every_parameter_the_console_fills_in_is_a_field_or_the_title(self) -> None:
        data = form(CONSOLE_REPORT_TEMPLATE)
        ids = set(fields(CONSOLE_REPORT_TEMPLATE))

        for parameter in CONSOLE_REPORT_PREFILLED:
            assert parameter == "title" or parameter in ids, parameter
        # The title is a default the person can still change, and one the console replaces.
        assert data["title"].strip()

    def test_the_file_the_console_downloads_has_a_box_to_be_dropped_into(self) -> None:
        attachment = fields(CONSOLE_REPORT_TEMPLATE)["attachment"]

        assert attachment["type"] == "textarea"
        assert attachment["validations"]["required"] is True
        text = (attachment["attributes"]["label"] + attachment["attributes"]["description"]).lower()
        assert "file" in text and "console" in text

    def test_it_does_not_ask_what_the_downloaded_file_already_holds(self) -> None:
        # The support report carries the installation and the platform; asking again is the
        # question a maintainer would otherwise have to send.
        ids = fields(CONSOLE_REPORT_TEMPLATE)

        assert "install" not in ids and "platform" not in ids and "support-report" not in ids

    def test_the_text_above_the_fields_sends_a_security_problem_elsewhere(self) -> None:
        intro = next(e for e in form(CONSOLE_REPORT_TEMPLATE)["body"] if e["type"] == "markdown")

        assert "SECURITY.md" in intro["attributes"]["value"]


class TestTheIssueChooser:
    def test_a_blank_issue_is_not_offered(self) -> None:
        assert load(FORMS / "config.yml")["blank_issues_enabled"] is False

    def test_it_sends_questions_to_discussions_and_vulnerabilities_to_the_private_report(
        self,
    ) -> None:
        links = {link["name"]: link for link in load(FORMS / "config.yml")["contact_links"]}

        urls = [link["url"] for link in links.values()]
        assert f"{REPOSITORY}/discussions/categories/q-a" in urls
        assert f"{REPOSITORY}/security/advisories/new" in urls
        assert "https://noust.dev/docs/" in urls
        assert len(urls) == 3
        for link in links.values():
            assert link["about"].strip()

    def test_security_md_names_the_private_report_the_chooser_links_to(self) -> None:
        text = read(ROOT / "SECURITY.md")

        assert f"{REPOSITORY}/security/advisories/new" in text
        assert "private vulnerability reporting" in text.lower()


class TestThePullRequestTemplate:
    TOPICS = {
        "tests": r"tests?\b.*\bfirst|pytest",
        "ruff and mypy": r"ruff.*mypy",
        "both languages": r"both languages|i18n",
        "the console's build": r"panel/.*(npm run build|rebuilt)",
        "the licence": r"licen[cs]e",
        "the agreement": r"\bCLA\b",
        "no secrets": r"secret",
    }

    def test_it_asks_for_the_short_list_of_checks_a_reviewer_would_ask_for(self) -> None:
        items = checkboxes(read(GITHUB / "PULL_REQUEST_TEMPLATE.md"))

        for topic, pattern in self.TOPICS.items():
            assert any(re.search(pattern, item, re.IGNORECASE) for item in items), topic
        # A list nobody reads to the end checks nothing.
        assert len(items) <= 9

    def test_it_closes_the_issue_it_comes_from(self) -> None:
        assert "Closes #" in read(GITHUB / "PULL_REQUEST_TEMPLATE.md")


class TestWhoReviews:
    def test_everything_goes_to_the_maintainer(self) -> None:
        lines = [
            line.split()
            for line in read(GITHUB / "CODEOWNERS").splitlines()
            if line.strip() and not line.startswith("#")
        ]

        assert lines == [["*", "@Perkybeet"]]


class TestTheCodeOfConduct:
    def test_it_is_the_contributor_covenant_2_1_with_the_contact_the_project_publishes(
        self,
    ) -> None:
        text = read(ROOT / "CODE_OF_CONDUCT.md")
        contributing = read(ROOT / "CONTRIBUTING.md")
        email = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", contributing)
        assert email is not None

        assert "Contributor Covenant" in text and "version 2.1" in text
        assert "[INSERT CONTACT METHOD]" not in text
        assert email.group(0) in text
        # CC BY 4.0: the credit and the address of the original go with the text.
        assert "contributor-covenant.org/version/2/1/code_of_conduct" in text

    def test_the_contributing_guide_links_it_and_the_support_page_names_it(self) -> None:
        assert "(CODE_OF_CONDUCT.md)" in read(ROOT / "CONTRIBUTING.md")
        assert "CODE_OF_CONDUCT.md" in read(ROOT / "docs" / "SUPPORT.md")


class TestTheAgreementOfAContribution:
    def test_it_says_what_the_contributing_guide_says(self) -> None:
        text = read(ROOT / "CLA.md")
        flat = " ".join(text.split())

        # The three points of "The licence of a contribution", in a lawyer's terms.
        assert "AGPL-3.0-or-later" in flat
        assert "right to submit" in flat.lower()
        assert re.search(r"perpetual.{0,80}irrevocable", flat, re.DOTALL)
        assert "Yago López Prado" in flat
        assert re.search(r"commercial", flat, re.IGNORECASE)
        assert re.search(r"AGPL.{0,40}stays", flat, re.IGNORECASE | re.DOTALL)
        assert "yago.lopez.adeje@gmail.com" in flat

    def test_it_tells_a_contributor_the_words_that_sign_it_and_where_the_signature_goes(
        self,
    ) -> None:
        flat = " ".join(read(ROOT / "CLA.md").split())

        assert SIGNATURE in flat
        assert SIGNATURES_BRANCH in flat

    def test_the_contributing_guide_links_it_and_keeps_its_own_statement(self) -> None:
        text = read(ROOT / "CONTRIBUTING.md")

        assert "(CLA.md)" in text
        assert "### The licence of a contribution" in text

    def test_the_pull_request_check_is_the_one_action_pinned_by_commit(self) -> None:
        raw = read(WORKFLOW)
        workflow = load(WORKFLOW)
        uses = [
            step["uses"]
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if "uses" in step
        ]

        assert len(uses) == 1
        action, _, ref = uses[0].partition("@")
        assert action == "contributor-assistant/github-action"
        assert SHA.fullmatch(ref)
        # Dependabot keeps the pin current and the comment says which version it is.
        assert re.search(rf"@{ref} # v\d+\.\d+\.\d+\n", raw)

    def test_it_runs_from_the_base_branch_and_never_checks_the_pull_request_out(self) -> None:
        workflow = load(WORKFLOW)
        workflow["on"] = workflow.pop(True, workflow.get("on"))
        steps = [step for job in workflow["jobs"].values() for step in job["steps"]]

        assert set(workflow["on"]) == {"pull_request_target", "issue_comment"}
        assert not any(str(step.get("uses", "")).startswith("actions/checkout") for step in steps)
        # Nothing of a fork's own text reaches a shell: there is no shell.
        assert not any("run" in step for step in steps)

    def test_its_token_can_do_what_the_check_does_and_nothing_else(self) -> None:
        workflow = load(WORKFLOW)
        job = next(iter(workflow["jobs"].values()))

        assert workflow["permissions"] == {"contents": "read"}
        assert job["permissions"] == CLA_JOB_PERMISSIONS
        step = job["steps"][0]
        assert step["env"] == {"GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}

    def test_it_stores_the_signatures_on_a_branch_of_their_own(self) -> None:
        settings = load(WORKFLOW)["jobs"]["cla"]["steps"][0]["with"]

        assert settings["branch"] == SIGNATURES_BRANCH
        # A pull request closed without merging is not locked behind the contributor.
        assert settings["lock-pullrequest-aftermerge"] == "false"
        assert settings["path-to-signatures"].endswith(".json")
        assert settings["path-to-document"] == f"{REPOSITORY}/blob/main/CLA.md"
        # Not the default branch: the ruleset that protects it must not stand in the way.
        assert settings["branch"] != "main"

    def test_the_allowlist_names_the_maintainer_and_the_bots_exactly(self) -> None:
        allowed = load(WORKFLOW)["jobs"]["cla"]["steps"][0]["with"]["allowlist"].split(",")

        # The action reads `*` as an unanchored pattern, so a wildcard would let in a person
        # whose name merely contains a bot's.
        assert allowed == CLA_ALLOWLIST
        assert not any("*" in name for name in allowed)

    def test_it_acts_on_a_pull_request_and_on_the_words_that_sign_or_ask_again(self) -> None:
        condition = load(WORKFLOW)["jobs"]["cla"]["if"]

        assert SIGNATURE in condition
        assert "'recheck'" in condition
        assert "github.event.issue.pull_request" in condition
        assert "pull_request_target" in condition


class TestWhatTheSupportPageSays:
    def test_it_describes_every_label_the_repository_defines(self) -> None:
        text = read(ROOT / "docs" / "SUPPORT.md")

        for name in defined_labels():
            assert f"`{name}`" in text, name

    def test_every_area_it_names_exists(self) -> None:
        text = read(ROOT / "docs" / "SUPPORT.md")

        for named in set(re.findall(r"`(area/[a-z-]+)`", text)):
            assert named in defined_labels(), named

    def test_it_follows_a_report_from_the_console_to_its_form(self) -> None:
        text = read(ROOT / "docs" / "SUPPORT.md")

        assert f"template={CONSOLE_REPORT_TEMPLATE}" in text
        assert "from-console" in text
        assert f"{REPOSITORY}/discussions" in text

    def test_it_points_the_labels_at_the_file_that_defines_them(self) -> None:
        assert "(../.github/labels.yml)" in read(ROOT / "docs" / "SUPPORT.md")


class TestNothingHereSpeaksOfWhoWroteIt:
    """
    The files of this area say nothing of who or what wrote them.

    The words are written backwards, as ``test_published_docs_names`` does, so that this file
    does not hold them. The one name that has to stay is the third-party action's own, in the
    line that pins it.
    """

    WORDS = (
        "edualc",
        "cipohtna",
        "tnega",
        "stnega",
        "tnatsissa",
        "eertkrow",
        "mll",
        "tolipoc",
        "ia",
    )
    OWNED = (
        ".github/ISSUE_TEMPLATE",
        ".github/labels.yml",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/CODEOWNERS",
        ".github/workflows/cla.yml",
        "CLA.md",
        "CODE_OF_CONDUCT.md",
        "docs/SUPPORT.md",
    )

    def test_no_word_of_the_list_is_in_a_file_of_this_area(self) -> None:
        paths: list[Path] = []
        for owned in self.OWNED:
            path = ROOT / owned
            paths += sorted(path.glob("*")) if path.is_dir() else [path]
        pattern = re.compile(
            r"\b(" + "|".join(word[::-1] for word in self.WORDS) + r")\b", re.IGNORECASE
        )

        found = []
        for path in paths:
            text = read(path).replace("contributor-assistant/github-action", "")
            found += [f"{path.relative_to(ROOT)}: {m.group(0)}" for m in pattern.finditer(text)]

        assert not found, "\n".join(found)
