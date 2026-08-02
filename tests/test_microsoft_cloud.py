import io
import os
import tempfile
import unittest
from unittest.mock import patch

import microsoft_cloud


class _Download:
    def __init__(self, value):
        self.raw = io.BytesIO(value)


class _FakeGraph:
    children = {
        "root": [
            {"id": "company", "name": "甲公司", "folder": {}},
            {"id": "top-file", "name": "总清单.xlsx", "file": {}},
        ],
        "company": [
            {"id": "evidence", "name": "银行函证", "folder": {}},
        ],
        "evidence": [
            {"id": "pdf", "name": "回函.pdf", "file": {}},
        ],
    }

    def __init__(self, token):
        self.token = token

    def paged(self, endpoint):
        item_id = endpoint.split("/items/", 1)[1].split("/", 1)[0]
        yield from self.children[item_id]

    def get(self, endpoint, stream=False):
        item_id = endpoint.split("/items/", 1)[1].split("/", 1)[0]
        return _Download(f"content:{item_id}".encode())


class MicrosoftCloudTests(unittest.TestCase):
    def test_share_id_is_graph_url_safe(self):
        share_id = microsoft_cloud._share_id("https://example.sharepoint.com/:f:/s/a?x=1")
        self.assertTrue(share_id.startswith("u!"))
        self.assertNotIn("=", share_id)

    def test_mirror_preserves_nested_folder_hierarchy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(microsoft_cloud, "GraphClient", _FakeGraph),
                patch.object(microsoft_cloud, "_access_token", return_value="token"),
                patch.object(
                    microsoft_cloud,
                    "resolve_folder",
                    return_value=("drive", {"id": "root", "name": "客户资料", "folder": {}}),
                ),
                patch.object(microsoft_cloud, "get_data_dir", side_effect=lambda name: os.path.join(temp_dir, name)),
            ):
                result = microsoft_cloud.mirror_folder(
                    "https://example.sharepoint.com/sites/a/Shared%20Documents/客户资料"
                )

            relative_files = {
                os.path.relpath(path, result["root_path"]) for path in result["files"]
            }
            relative_folders = {
                os.path.relpath(path, result["root_path"]) for path in result["folders"]
            }
            self.assertEqual(
                relative_folders,
                {os.path.join("甲公司"), os.path.join("甲公司", "银行函证")},
            )
            self.assertEqual(
                relative_files,
                {"总清单.xlsx", os.path.join("甲公司", "银行函证", "回函.pdf")},
            )
            with open(os.path.join(result["root_path"], "甲公司", "银行函证", "回函.pdf"), "rb") as stream:
                self.assertEqual(stream.read(), b"content:pdf")


if __name__ == "__main__":
    unittest.main()
