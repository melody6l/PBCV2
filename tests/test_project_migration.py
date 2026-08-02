import unittest

from project_manager import _deserialize_state, _serialize_state


class ProjectMigrationTests(unittest.TestCase):
    def test_version_one_project_gets_continuity_defaults(self):
        legacy = {
            "version": 1,
            "checklist": [{"需求": "序时账"}],
            "scanned_files": ["/资料/序时账.xlsx"],
            "match_results": [{"status": "已获取"}],
        }

        state = _deserialize_state(legacy)

        self.assertEqual(state["scanned_files"], ["/资料/序时账.xlsx"])
        self.assertEqual(state["scan_sources"], [])
        self.assertEqual(state["documents"], {})
        self.assertEqual(state["folder_requirement_mappings"], [])
        self.assertEqual(state["scan_file_statuses"], [])

    def test_new_registry_fields_round_trip(self):
        state = {
            "scan_sources": [{"id": "source-1", "role": "organized"}],
            "documents": {"document-1": {"preferred_path": "/整理/序时账.xlsx"}},
            "scan_snapshots": {"source-1": {"paths": []}},
            "folder_requirement_mappings": [{"folder": "/整理/序时账", "row_index": 1}],
            "organize_mappings": [{"source": "/资料/序时账.xlsx", "target": "/整理/序时账.xlsx"}],
            "scan_file_statuses": [{"path": "/资料/a.zip", "status": "extracted"}],
        }

        restored = _deserialize_state(_serialize_state(state))

        for key in state:
            self.assertEqual(restored[key], state[key])


if __name__ == "__main__":
    unittest.main()
