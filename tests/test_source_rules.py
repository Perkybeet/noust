# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What decides whether a push or a pull request deploys (checklist A-13, A-28).

``[skip ci]`` and ``[skip cd]`` in commit messages, and watch paths: globs over
the files a push changed, with ``!`` negation where the last pattern that
matches wins. Both are pure: the same answer from a payload of any forge.
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import ValidationError
from noust.core.source_rules import (
    normalize_watch_paths,
    pull_request_skip_reason,
    push_skip_reason,
    watch_paths_match,
    watch_paths_trigger,
)


class TestPushSkip:
    def test_every_commit_marked_skips(self) -> None:
        reason = push_skip_reason(["docs: fix typo [skip ci]", "wip [SKIP CD]"])
        assert reason is not None
        assert "skip" in reason.lower()

    def test_one_unmarked_commit_deploys(self) -> None:
        assert push_skip_reason(["docs [skip ci]", "feat: the real change"]) is None

    def test_no_message_deploys(self) -> None:
        assert push_skip_reason([]) is None
        assert push_skip_reason(["", "   "]) is None

    def test_markers_are_case_insensitive_and_anywhere(self) -> None:
        assert push_skip_reason(["[Skip CI] first line\n\nbody"]) is not None
        assert push_skip_reason(["title\n\nbody ... [skip cd]"]) is not None

    def test_similar_text_does_not_skip(self) -> None:
        assert push_skip_reason(["skip ci"]) is None
        assert push_skip_reason(["[skip]"]) is None
        assert push_skip_reason(["[skip-ci]"]) is None

    def test_a_list_that_may_be_cut_short_never_skips(self) -> None:
        # The commits the payload omits may not be marked.
        assert push_skip_reason(["[skip ci]"], complete=False) is None


class TestPullRequestSkip:
    def test_the_title_alone_skips(self) -> None:
        assert pull_request_skip_reason("Update readme [skip ci]", "") is not None

    def test_the_head_commit_alone_skips(self) -> None:
        assert pull_request_skip_reason("Update readme", "typo [skip cd]") is not None

    def test_neither_deploys(self) -> None:
        assert pull_request_skip_reason("Update readme", "typo") is None
        assert pull_request_skip_reason("", "") is None


class TestWatchPathsNormalised:
    def test_lines_and_lists_are_the_same_thing(self) -> None:
        assert normalize_watch_paths("apps/web/**\n!apps/web/docs/**\n") == (
            "apps/web/**",
            "!apps/web/docs/**",
        )
        assert normalize_watch_paths(["apps/web/**", " !apps/web/docs/** ", ""]) == (
            "apps/web/**",
            "!apps/web/docs/**",
        )

    def test_a_leading_slash_means_the_root_of_the_repository(self) -> None:
        assert normalize_watch_paths(["/apps/web/**", "!/docs/**"]) == ("apps/web/**", "!docs/**")

    def test_comments_and_duplicates_are_dropped(self) -> None:
        assert normalize_watch_paths(["# the web app", "a/**", "a/**"]) == ("a/**",)

    def test_nothing_is_none(self) -> None:
        assert normalize_watch_paths(None) == ()
        assert normalize_watch_paths("") == ()
        assert normalize_watch_paths(["", " "]) == ()

    @pytest.mark.parametrize(
        "bad",
        [
            "../secret/**",
            "a/../../b",
            "a/\x00b",
            "x" * 400,
            "!",
            "a\\b",
        ],
    )
    def test_refuses_what_is_not_a_path_inside_the_repository(self, bad: str) -> None:
        with pytest.raises(ValidationError) as caught:
            normalize_watch_paths([bad])
        assert caught.value.field == "watch_paths"

    def test_refuses_too_many(self) -> None:
        with pytest.raises(ValidationError):
            normalize_watch_paths([f"dir{i}/**" for i in range(200)])


class TestGlobs:
    @pytest.mark.parametrize(
        ("pattern", "path", "expected"),
        [
            ("apps/web/**", "apps/web/src/index.ts", True),
            ("apps/web/**", "apps/web/package.json", True),
            ("apps/web/**", "apps/api/src/index.ts", False),
            ("apps/*/package.json", "apps/web/package.json", True),
            ("apps/*/package.json", "apps/web/src/package.json", False),
            ("**/*.md", "README.md", True),
            ("**/*.md", "docs/guide/intro.md", True),
            ("*.md", "docs/intro.md", False),
            ("src/?.ts", "src/a.ts", True),
            ("src/?.ts", "src/ab.ts", False),
            ("src/[ab].ts", "src/b.ts", True),
            ("src/[ab].ts", "src/c.ts", False),
            ("src/[!ab].ts", "src/c.ts", True),
            ("src/[!ab].ts", "src/a.ts", False),
            ("package.json", "package.json", True),
            ("package.json", "apps/web/package.json", False),
            ("a.b", "aXb", False),
            ("apps/web/", "apps/web/src/main.ts", True),
            ("apps/web", "apps/web/src/main.ts", True),
            ("apps/web", "apps/webextra/main.ts", False),
        ],
    )
    def test_matches_the_way_gitignore_users_expect(
        self, pattern: str, path: str, expected: bool
    ) -> None:
        assert watch_paths_match((pattern,), path) is expected

    def test_the_last_matching_pattern_wins(self) -> None:
        patterns = ("apps/web/**", "!apps/web/docs/**", "apps/web/docs/api.ts")
        assert watch_paths_match(patterns, "apps/web/src/a.ts") is True
        assert watch_paths_match(patterns, "apps/web/docs/readme.md") is False
        assert watch_paths_match(patterns, "apps/web/docs/api.ts") is True

    def test_only_exclusions_include_everything_else(self) -> None:
        patterns = ("!docs/**", "!*.md")
        assert watch_paths_match(patterns, "src/a.ts") is True
        assert watch_paths_match(patterns, "docs/a.ts") is False
        assert watch_paths_match(patterns, "README.md") is False

    def test_an_unmatched_path_with_inclusions_is_out(self) -> None:
        assert watch_paths_match(("apps/web/**", "!apps/web/docs/**"), "apps/api/x.ts") is False


class TestTrigger:
    def test_any_included_file_triggers(self) -> None:
        patterns = ("apps/web/**",)
        assert watch_paths_trigger(patterns, ["README.md", "apps/web/a.ts"]) is True
        assert watch_paths_trigger(patterns, ["README.md", "apps/api/a.ts"]) is False

    def test_no_patterns_always_deploys(self) -> None:
        assert watch_paths_trigger((), ["anything"]) is True

    def test_unknown_files_always_deploy(self) -> None:
        assert watch_paths_trigger(("apps/web/**",), None) is True

    def test_an_empty_known_list_does_not_deploy(self) -> None:
        # A push that changed no file (an empty commit) has nothing to watch.
        assert watch_paths_trigger(("apps/web/**",), []) is False
