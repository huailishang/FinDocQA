from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evidence.workspace_bundle_adapter import WorkspaceBundleAdapter


def valid_args() -> dict:
    return {
        "question_payload": {"qid": "q_demo", "question": "What was revenue?"},
        "document_family": "annual_demo_2024",
        "workspace_pages": [2, 5],
        "page_text_by_number": {2: "Revenue was 10.", 5: "Additional context."},
        "provenance_by_page": {"2": ["lexical", "semantic"], "5": ["semantic"]},
        "scope_policy": {
            "fail_closed_on_outside_page": True,
            "fail_closed_on_unknown_page": True,
            "full_document_fallback_allowed": False,
            "max_unique_pages": 10,
            "page_index_semantics": "ONE_BASED_CANONICAL_PHYSICAL_PAGE",
        },
        "workspace_kind": "deduplicated_union",
    }


class WorkspaceBundleAdapterTests(unittest.TestCase):
    def test_happy_path_is_deterministic_and_preserves_scope(self) -> None:
        adapter = WorkspaceBundleAdapter()
        first = adapter.build(**valid_args())
        second = adapter.build(**valid_args())

        self.assertEqual(first.question.qid, "q_demo")
        self.assertEqual(tuple(first.question.doc_ids), ("annual_demo_2024",))
        self.assertEqual(first.question.answer_format, "freeform")
        self.assertEqual(first.workspace_scope_sha256, second.workspace_scope_sha256)
        self.assertEqual(first.workspace_scope_id, second.workspace_scope_id)
        self.assertEqual(len(first.workspace_scope_sha256), 64)
        self.assertTrue(first.workspace_scope_id.startswith("workspace_"))
        self.assertEqual(
            [c.metadata["canonical_physical_page"] for c in first.bundle.candidates],
            [2, 5],
        )
        self.assertEqual(
            [c.metadata["canonical_physical_page"] for c in first.bundle.verification_candidates],
            [2, 5],
        )
        self.assertTrue(first.bundle.prompt_context.strip())
        self.assertEqual(first.bundle.metadata["workspace_pages"], [2, 5])

    def test_hash_changes_when_text_or_provenance_changes(self) -> None:
        adapter = WorkspaceBundleAdapter()
        base = valid_args()
        original = adapter.build(**base)

        changed_text = valid_args()
        changed_text["page_text_by_number"] = dict(changed_text["page_text_by_number"])
        changed_text["page_text_by_number"][2] += " changed"
        self.assertNotEqual(
            original.workspace_scope_sha256,
            adapter.build(**changed_text).workspace_scope_sha256,
        )

        changed_provenance = valid_args()
        changed_provenance["provenance_by_page"] = dict(changed_provenance["provenance_by_page"])
        changed_provenance["provenance_by_page"]["2"] = ["lexical"]
        self.assertNotEqual(
            original.workspace_scope_sha256,
            adapter.build(**changed_provenance).workspace_scope_sha256,
        )

    def test_mandatory_fail_closed_cases(self) -> None:
        adapter = WorkspaceBundleAdapter()
        cases = []

        value = valid_args()
        value["question_payload"] = {"question": "What was revenue?"}
        cases.append(value)

        value = valid_args()
        value["document_family"] = ""
        cases.append(value)

        value = valid_args()
        value["workspace_pages"] = [2, 2]
        cases.append(value)

        value = valid_args()
        value["page_text_by_number"] = {5: "Additional context."}
        cases.append(value)

        value = valid_args()
        value["provenance_by_page"] = {"5": ["semantic"]}
        cases.append(value)

        value = valid_args()
        value["scope_policy"] = dict(value["scope_policy"])
        value["scope_policy"]["max_unique_pages"] = 1
        cases.append(value)

        value = valid_args()
        value["scope_policy"] = dict(value["scope_policy"])
        value["scope_policy"]["page_index_semantics"] = "ZERO_BASED"
        cases.append(value)

        value = valid_args()
        value["scope_policy"] = dict(value["scope_policy"])
        value["scope_policy"]["full_document_fallback_allowed"] = True
        cases.append(value)

        for args in cases:
            with self.subTest(args=args):
                with self.assertRaises((TypeError, ValueError)):
                    adapter.build(**args)


if __name__ == "__main__":
    unittest.main()
