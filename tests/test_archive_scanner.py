import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import archive_scanner


class ArchiveScannerTests(unittest.TestCase):
    def test_zip_is_expanded_and_nested_files_are_returned(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            archive_path = os.path.join(root, "客户资料.zip")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("公司1/银行函证/回函.pdf", b"pdf")
            with patch.object(
                archive_scanner,
                "get_data_dir",
                side_effect=lambda *parts: os.path.join(cache, *parts),
            ):
                result = archive_scanner.expand_archives(root, [archive_path])
            self.assertEqual(result["statuses"][0]["status"], "extracted")
            relative_names = [os.path.relpath(path, result["cache_root"]) for path in result["files"]]
            self.assertTrue(any(name.endswith(os.path.join("公司1", "银行函证", "回函.pdf"))
                                for name in relative_names))

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            archive_path = os.path.join(root, "bad.zip")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.txt", b"no")
            with patch.object(
                archive_scanner,
                "get_data_dir",
                side_effect=lambda *parts: os.path.join(cache, *parts),
            ):
                result = archive_scanner.expand_archives(root, [archive_path])
            self.assertEqual(result["statuses"][0]["status"], "failed")

    def test_7z_is_expanded_without_external_software(self):
        import py7zr
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            source = os.path.join(root, "回函.pdf")
            with open(source, "wb") as stream:
                stream.write(b"pdf")
            archive_path = os.path.join(root, "客户资料.7z")
            with py7zr.SevenZipFile(archive_path, "w") as archive:
                archive.write(source, arcname="公司1/回函.pdf")
            with patch.object(
                archive_scanner,
                "get_data_dir",
                side_effect=lambda *parts: os.path.join(cache, *parts),
            ):
                result = archive_scanner.expand_archives(root, [archive_path])
            self.assertEqual(result["statuses"][0]["status"], "extracted")
            self.assertTrue(any(path.endswith(os.path.join("公司1", "回函.pdf"))
                                for path in result["files"]))


if __name__ == "__main__":
    unittest.main()
