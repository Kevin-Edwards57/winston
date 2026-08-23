import unittest
from unittest.mock import Mock, patch

import winston_app


class LocalScraperTests(unittest.TestCase):
    def test_discovers_same_domain_contact_pages_only(self):
        html = """
        <a href="/contact-us">Contact our team</a>
        <a href="https://example.com/about">About</a>
        <a href="https://tracking.example.net/contact">Contact tracker</a>
        """
        pages = winston_app.discover_contact_pages("https://example.com", html)
        self.assertIn("https://example.com/contact-us", pages)
        self.assertIn("https://example.com/about", pages)
        self.assertNotIn("https://tracking.example.net/contact", pages)

    def test_extracts_only_explicit_social_urls(self):
        html = """
        <script>window.formatjs = true; window.wix = true;</script>
        <a href="https://instagram.com/real_business">Instagram</a>
        <a href="mailto:owner@example.com">Email</a>
        """
        social = winston_app.extract_social_handles(html)
        self.assertEqual(social.get("instagram"), "real_business")
        self.assertNotIn("formatjs", social.values())
        self.assertNotIn("wix", social.values())

    @patch("winston_app.requests.get")
    def test_local_fetch_rejects_non_html_and_large_pages(self, get):
        response = Mock(status_code=200, headers={"Content-Type": "application/json"}, content=b"{}", text="{}")
        get.return_value = response
        self.assertEqual(winston_app.fetch_local_html("https://example.com/data"), "")
        response.headers = {"Content-Type": "text/html"}
        response.content = b"x" * (winston_app.MAX_LOCAL_PAGE_BYTES + 1)
        self.assertEqual(winston_app.fetch_local_html("https://example.com"), "")


if __name__ == "__main__":
    unittest.main()
