"""Offline HTTP coverage for public pages layered over the Streamlit server."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from portal.education_routes import install_routes
from portal import run
from utils import education_public


class ExistingApplicationHandler(tornado.web.RequestHandler):
    def get(self, path):
        self.write("existing:" + path)


class PublicRouteTests(AsyncHTTPTestCase):
    def get_app(self):
        return install_routes(tornado.web.Application([
            (r"/(.*)", ExistingApplicationHandler),
        ]))

    def test_public_pages_deliver_initial_html_without_session(self):
        for path, key in [
            ("/education", "education"),
            ("/education/ai-ed-shorts", "ai-ed-shorts"),
            ("/privacy/ai-ed-shorts", "privacy"),
        ]:
            for suffix in ("", "/"):
                with self.subTest(path=path + suffix):
                    response = self.fetch(path + suffix)
                    self.assertEqual(response.code, 200)
                    self.assertIn("text/html", response.headers["Content-Type"])
                    self.assertEqual(response.body.decode(), education_public.render_document(key))
                    self.assertIn("<title>", response.body.decode())
                    self.assertIn("canonical", response.body.decode())
                    self.assertNotIn("Set-Cookie", response.headers)

    def test_existing_routes_remain_owned_by_original_application(self):
        for path in ("Home", "Discovery", "Contact", "_stcore/health", "_stcore/stream"):
            with self.subTest(path=path):
                response = self.fetch("/" + path)
                self.assertEqual(response.body.decode(), "existing:" + path)

    def test_unknown_paths_and_traversal_are_not_education_assets(self):
        for path in (
            "education/unpublished", "education-extra", "privacy/other",
            "education-assets/unknown.css", "education-assets/../app.py",
            "education-assets/%2e%2e%2fapp.py", "education-assets/education.css/extra",
        ):
            with self.subTest(path=path):
                response = self.fetch("/" + path)
                self.assertTrue(response.body.decode().startswith("existing:"))

    def test_allowlisted_assets_are_css(self):
        for asset in ("stock-sentinel-tokens.css", "stock-sentinel-components.css", "education.css"):
            response = self.fetch("/education-assets/" + asset)
            self.assertEqual(response.code, 200)
            self.assertIn("text/css", response.headers["Content-Type"])
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertGreater(len(response.body), 20)

    def test_search_engine_endpoints(self):
        response = self.fetch("/sitemap.xml")
        self.assertEqual(response.code, 200)
        self.assertEqual(response.body.decode(), education_public.sitemap_xml())
        self.assertIn("application/xml", response.headers["Content-Type"])
        response = self.fetch("/robots.txt")
        self.assertIn(education_public.PRODUCTION_ORIGIN + "/sitemap.xml", response.body.decode())

    def test_head_delivers_public_headers_without_body(self):
        for path in (
            "/education", "/education/ai-ed-shorts", "/privacy/ai-ed-shorts",
            "/education-assets/education.css", "/robots.txt", "/sitemap.xml",
        ):
            with self.subTest(path=path):
                get_response = self.fetch(path)
                head_response = self.fetch(path, method="HEAD")
                self.assertEqual(head_response.code, 200)
                self.assertEqual(head_response.body, b"")
                self.assertEqual(head_response.headers["Content-Type"], get_response.headers["Content-Type"])
                self.assertEqual(head_response.headers["Content-Length"], get_response.headers["Content-Length"])

    def test_unsupported_method_does_not_modify_content(self):
        response = self.fetch("/education", method="POST", body="")
        self.assertEqual(response.code, 405)


class LauncherTests(unittest.TestCase):
    def test_wraps_original_app_once_and_preserves_its_settings(self):
        app = tornado.web.Application([], cookie_secret="offline", xsrf_cookies=True)
        with patch.object(run.Server, "_create_app", return_value=app) as original:
            original._stock_sentinel_education = False
            run.install_public_routes()
            wrapped = run.Server._create_app
            run.install_public_routes()
            self.assertIs(run.Server._create_app, wrapped)
            sentinel = object()
            self.assertIs(wrapped(sentinel), app)
            original.assert_called_once_with(sentinel)
            self.assertTrue(app.settings["xsrf_cookies"])
            self.assertEqual(app.settings["cookie_secret"], "offline")

    def test_unsupported_streamlit_version_requires_review(self):
        with patch.object(run.streamlit, "__version__", "999.0.0"):
            with self.assertRaisesRegex(RuntimeError, "verify the server hook"):
                run.install_public_routes()


if __name__ == "__main__":
    unittest.main()
