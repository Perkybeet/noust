# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JSON with comments, as ``deno.jsonc`` is written (checklist A-40).

A repository's configuration is parsed, never evaluated, and a ``//`` inside a string is part of
the string: the scanner that removes comments must know where a string is.
"""

from __future__ import annotations

import pytest

from noust.deployers.helpers import jsonc


class TestComments:
    def test_a_line_comment_and_a_block_comment_are_removed(self) -> None:
        text = '{\n  // the tasks\n  "a": 1, /* inline */ "b": 2\n}'
        assert jsonc.loads(text) == {"a": 1, "b": 2}

    def test_a_double_slash_inside_a_string_is_not_a_comment(self) -> None:
        assert jsonc.loads('{"url": "https://deno.land/std/mod.ts"}') == {
            "url": "https://deno.land/std/mod.ts"
        }

    def test_a_block_comment_marker_inside_a_string_is_not_a_comment(self) -> None:
        assert jsonc.loads('{"glob": "src/**/*.ts"}') == {"glob": "src/**/*.ts"}

    def test_an_escaped_quote_does_not_end_the_string(self) -> None:
        assert jsonc.loads(r'{"a": "say \"// hi\"", "b": 1}') == {"a": 'say "// hi"', "b": 1}

    def test_an_unterminated_block_comment_runs_to_the_end(self) -> None:
        assert jsonc.loads('{"a": 1} /* never closed') == {"a": 1}

    def test_line_breaks_are_kept_so_an_error_points_at_the_right_line(self) -> None:
        stripped = jsonc.strip_comments('{\n/* one\ntwo */\n"a": }')
        assert stripped.count("\n") == 3


class TestTrailingCommas:
    def test_a_trailing_comma_in_an_object_and_in_an_array(self) -> None:
        assert jsonc.loads('{"a": [1, 2,], "b": {"c": 3,},}') == {"a": [1, 2], "b": {"c": 3}}

    def test_a_comma_inside_a_string_is_kept(self) -> None:
        assert jsonc.loads('{"a": "x,}"}') == {"a": "x,}"}

    def test_a_comma_before_a_comment_and_a_closing_bracket(self) -> None:
        assert jsonc.loads('{"a": 1, // last\n}') == {"a": 1}


class TestRefusals:
    @pytest.mark.parametrize("text", ["", "{", "not json", '{"a": }', "{'a': 1}"])
    def test_what_is_not_json_is_a_value_error(self, text: str) -> None:
        with pytest.raises(ValueError):
            jsonc.loads(text)

    def test_a_document_larger_than_a_configuration_file_is_refused(self) -> None:
        with pytest.raises(ValueError, match="larger"):
            jsonc.loads('{"a": "' + "x" * jsonc.MAX_BYTES + '"}')
