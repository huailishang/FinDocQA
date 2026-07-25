from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from agent.factory import PipelineFactory
from data.loader import JsonQuestionLoader


B_QUESTIONS_DIR = Path("../data/upload_b/question_b")




def test_loader_reports_duplicate_qid_with_source_location(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text(
        '{"qid":"q1","domain":"research","question":"first","type":"计算题","options":{}}\n'
        '{"qid":"q1","domain":"research","question":"second","type":"计算题","options":{}}\n',
        encoding="utf-8-sig",
    )

    with pytest.raises(ValueError, match=r"duplicate qid.*a\.jsonl:2"):
        JsonQuestionLoader(tmp_path).load()


def test_loader_rejects_missing_required_fields(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text(
        '[{"qid":"","domain":"research","question":"x","type":"计算题","options":{}}]',
        encoding="utf-8-sig",
    )

    with pytest.raises(ValueError, match="qid must be non-empty"):
        JsonQuestionLoader(tmp_path).load()




def test_factory_prefers_existing_explicit_questions_dir(tmp_path: Path) -> None:
    explicit = tmp_path / "upload_b/question_b"
    explicit.mkdir(parents=True)
    factory = PipelineFactory(
        config={
            "paths": {
                "questions_dir": "upload_b/question_b",
                "raw_dataset": "data/raw_dataset",
                "question_group": "group_a",
            }
        },
        project_root=tmp_path,
    )

    assert factory.build_loader().questions_dir == explicit.resolve()


def test_factory_falls_back_when_explicit_questions_dir_is_missing(tmp_path: Path) -> None:
    factory = PipelineFactory(
        config={
            "paths": {
                "questions_dir": "missing/question_b",
                "raw_dataset": "data/raw_dataset",
                "question_group": "group_a",
            }
        },
        project_root=tmp_path,
    )

    assert factory.build_loader().questions_dir == (
        tmp_path / "data/raw_dataset/questions/group_a"
    ).resolve()
