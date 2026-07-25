from pathlib import Path

from agent.factory import PipelineFactory


def test_question_group_defaults_to_group_a(tmp_path: Path) -> None:
    factory = PipelineFactory(
        config={"paths": {"raw_dataset": "data/raw_dataset"}},
        project_root=tmp_path,
    )

    loader = factory.build_loader()

    assert loader.questions_dir == (tmp_path / "data/raw_dataset/questions/group_a").resolve()


def test_question_group_can_switch_to_group_b(tmp_path: Path) -> None:
    factory = PipelineFactory(
        config={
            "paths": {
                "raw_dataset": "data/raw_dataset",
                "question_group": "group_b",
            }
        },
        project_root=tmp_path,
    )

    loader = factory.build_loader()

    assert loader.questions_dir == (tmp_path / "data/raw_dataset/questions/group_b").resolve()
