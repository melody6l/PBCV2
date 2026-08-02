import os
import tempfile
import uuid
import unittest
from unittest.mock import patch

import app as app_module
from document_registry import reconcile_source, register_source


class OrganizeContinuityTests(unittest.TestCase):
    def test_conflict_keeps_both_and_rewrites_match_to_organized_path(self):
        with tempfile.TemporaryDirectory() as source_root, tempfile.TemporaryDirectory() as target:
            source_file = os.path.join(source_root, "序时账.xlsx")
            with open(source_file, "wb") as stream:
                stream.write(b"new version")
            conflict_dir = os.path.join(target, "财务数据", "序时账")
            os.makedirs(conflict_dir)
            existing = os.path.join(conflict_dir, "序时账.xlsx")
            with open(existing, "wb") as stream:
                stream.write(b"old version")

            sid = "test_" + uuid.uuid4().hex
            state = app_module.session_store.get(sid)
            state.clear()
            state.update({
                "checklist_template": {
                    "company_names": ["公司1"],
                    "items": [{
                        "row_index": 1,
                        "subject": "财务数据",
                        "demand_name": "序时账",
                    }],
                },
                "match_results": [{
                    "index": 1,
                    "status": "已获取",
                    "matched_files": [source_file],
                    "matched_names": ["序时账.xlsx"],
                    "matched_types": ["文件"],
                    "company_coverage": {},
                    "source_key": "序时账",
                }],
                "scanned_files": [source_file],
                "scanned_folders": [],
            })
            source = register_source(state, source_root)
            reconcile_source(state, source, [source_file])
            client = app_module.app.test_client()
            headers = {"X-Session-Id": sid}

            preflight = client.post(
                "/api/organize-files",
                headers=headers,
                json={"target_path": target, "dry_run": True},
            )
            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(preflight.get_json()["conflict_count"], 1)

            response = client.post(
                "/api/organize-files",
                headers=headers,
                json={"target_path": target, "conflict_policy": "keep_both"},
            )
            data = response.get_json()
            self.assertEqual(response.status_code, 200)
            versioned = os.path.join(conflict_dir, "序时账_v2.xlsx")
            self.assertTrue(os.path.isfile(existing))
            self.assertTrue(os.path.isfile(versioned))
            self.assertEqual(data["match_results"][0]["matched_files"], [versioned])
            self.assertTrue(any(item["role"] == "organized" for item in data["scan_sources"]))
            document = next(iter(state["documents"].values()))
            self.assertEqual(len(document["versions"]), 2)
            self.assertEqual(document["preferred_path"], versioned)

            later_file = os.path.join(conflict_dir, "补充序时账.xlsx")
            with open(later_file, "wb") as stream:
                stream.write(b"later")
            with tempfile.TemporaryDirectory() as archive_cache:
                with patch(
                    "archive_scanner.get_data_dir",
                    side_effect=lambda *parts: os.path.join(archive_cache, *parts),
                ):
                    scan = client.post(
                        "/api/scan-folder",
                        headers=headers,
                        json={"folder_path": target},
                    )
            scan_data = scan.get_json()
            self.assertEqual(scan.status_code, 200)
            self.assertFalse(scan_data["match_required"])
            self.assertIn(later_file, state["match_results"][0]["matched_files"])
            self.assertTrue(state["match_results"][0]["needs_confirmation"])


if __name__ == "__main__":
    unittest.main()
