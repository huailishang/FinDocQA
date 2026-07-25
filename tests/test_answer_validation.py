from __future__ import annotations

import pytest

from solvers.base import validate_submission_answer


@pytest.mark.parametrize(
    ("answer_format", "candidate", "valid", "canonical"),
    [
        ("mcq", "A", True, "A"),
        ("mcq", "D", True, "D"),
        ("mcq", "AB", False, "AB"),
        ("mcq", "ABCD", False, "ABCD"),
        ("tf", "A", True, "A"),
        ("tf", "B", True, "B"),
        ("tf", "C", False, "C"),
        ("tf", "AB", False, "AB"),
        ("multi", "A", True, "A"),
        ("multi", "AB", True, "AB"),
        ("multi", "ABD", True, "ABD"),
        ("multi", "ABCD", True, "ABCD"),
        ("multi", "AABC", True, "ABC"),
        ("multi", "DBA", True, "ABD"),
        ("multi", "AE", False, "AE"),
        ("unknown", "A", False, "A"),
    ],
)
def test_format_aware_answer_matrix(answer_format, candidate, valid, canonical):
    result = validate_submission_answer(candidate, answer_format)
    assert result.valid is valid
    assert result.answer == canonical
