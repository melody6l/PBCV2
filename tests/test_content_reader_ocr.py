import os
import tempfile
import unittest
from unittest.mock import patch

import content_reader


class OcrProviderTests(unittest.TestCase):
    def test_provider_credentials_are_validated_separately(self):
        self.assertTrue(content_reader._ocr_configured({
            "provider": "baidu", "api_key": "key", "secret_key": "secret"
        }))
        self.assertTrue(content_reader._ocr_configured({
            "provider": "aliyun", "access_key_id": "id", "access_key_secret": "secret"
        }))
        self.assertFalse(content_reader._ocr_configured({
            "provider": "aliyun", "api_key": "key", "secret_key": "secret"
        }))

    def test_switching_provider_does_not_reuse_ocr_cache(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image_file:
            image_file.write(b"test")
            image_path = image_file.name
        cache = {}

        def fake_extract(_path, config):
            return {
                "content": config["provider"], "content_type": "ocr",
                "content_label": "test", "truncated": False, "error": None,
            }

        try:
            with patch.object(content_reader, "extract_content", side_effect=fake_extract):
                baidu, cache = content_reader.extract_contents(
                    [image_path], {"provider": "baidu"}, cache
                )
                aliyun, cache = content_reader.extract_contents(
                    [image_path], {"provider": "aliyun"}, cache
                )
            self.assertEqual(baidu[image_path]["content"], "baidu")
            self.assertEqual(aliyun[image_path]["content"], "aliyun")
            self.assertEqual(len(cache), 2)
        finally:
            os.unlink(image_path)


if __name__ == "__main__":
    unittest.main()
