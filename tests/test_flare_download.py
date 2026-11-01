"""Offline checks for GitHub authentication and actionable rate-limit errors."""
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError


spec = importlib.util.spec_from_file_location(
    "flare_download", Path(__file__).resolve().parents[1] / "scripts/download_flare_sources.py")
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


class FlareDownloadTests(unittest.TestCase):
    def test_tree_uses_environment_token(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"tree": []}'
        with patch.dict(downloader.os.environ, {"GITHUB_TOKEN": "test-only-token"}, clear=True), \
                patch.object(downloader, "urlopen", return_value=response) as opened:
            self.assertEqual(downloader.fetch_tree("flare-game", "revision"), b'{"tree": []}')
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-token")
        self.assertEqual(request.host, "api.github.com")

    def test_rate_limit_has_retry_guidance_without_token(self):
        error = HTTPError("https://api.github.com/", 403, "rate limit exceeded", {}, io.BytesIO())
        with patch.dict(downloader.os.environ, {"GITHUB_TOKEN": "test-only-token"}, clear=True), \
                patch.object(downloader, "urlopen", side_effect=error):
            with self.assertRaises(SystemExit) as caught:
                downloader.fetch_tree("flare-game", "revision")
        self.assertIn("GITHUB_TOKEN", str(caught.exception))
        self.assertNotIn("test-only-token", str(caught.exception))
