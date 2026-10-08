import unittest

from app import _select_update_asset


class UpdateAssetTests(unittest.TestCase):
    def test_prefers_app_only_update_when_release_has_both_packages(self):
        assets = [
            {
                "name": "OBSAIHighlights-Setup-v1.0.6.exe",
                "browser_download_url": "https://example.invalid/full.exe",
                "size": 1000,
            },
            {
                "name": "OBSAIHighlights-Update-v1.0.6.exe",
                "browser_download_url": "https://example.invalid/update.exe",
                "size": 100,
            },
        ]

        self.assertEqual(
            _select_update_asset(assets),
            ("https://example.invalid/update.exe", 100),
        )

    def test_falls_back_to_full_installer_for_older_releases(self):
        assets = [{
            "name": "OBSAIHighlights-Setup-v1.0.5.exe",
            "browser_download_url": "https://example.invalid/full.exe",
            "size": 1000,
        }]

        self.assertEqual(
            _select_update_asset(assets),
            ("https://example.invalid/full.exe", 1000),
        )

    def test_ignores_unrelated_release_assets(self):
        self.assertEqual(
            _select_update_asset([{
                "name": "checksums.txt",
                "browser_download_url": "https://example.invalid/checksums.txt",
                "size": 20,
            }]),
            (None, None),
        )


if __name__ == "__main__":
    unittest.main()
