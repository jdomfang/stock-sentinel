"""Exact public HTML routes layered ahead of Streamlit's unchanged handlers."""
from pathlib import Path

import tornado.web

from utils import education_public


_ASSETS = {
    "stock-sentinel-tokens.css": "stock-sentinel-tokens.css",
    "stock-sentinel-components.css": "stock-sentinel-components.css",
    "education.css": "education.css",
}
_STYLE_DIR = Path(__file__).resolve().parents[1] / "assets" / "styles"


class PublicReadOnlyHandler(tornado.web.RequestHandler):
    def head(self, *args, **kwargs):
        # Tornado suppresses the response body for HEAD while preserving headers.
        self.get(*args, **kwargs)


class EducationPageHandler(PublicReadOnlyHandler):
    def initialize(self, page_key):
        self.page_key = page_key

    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("X-Content-Type-Options", "nosniff")
        self.write(education_public.render_document(self.page_key))


class EducationAssetHandler(PublicReadOnlyHandler):
    def get(self, name):
        # Route matching and this mapping independently restrict filesystem access.
        filename = _ASSETS.get(name)
        if filename is None:
            raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "text/css; charset=utf-8")
        self.set_header("X-Content-Type-Options", "nosniff")
        self.write((_STYLE_DIR / filename).read_text(encoding="utf-8"))


class EducationSitemapHandler(PublicReadOnlyHandler):
    def get(self):
        self.set_header("Content-Type", "application/xml; charset=utf-8")
        self.write(education_public.sitemap_xml())


class EducationRobotsHandler(PublicReadOnlyHandler):
    def get(self):
        self.set_header("Content-Type", "text/plain; charset=utf-8")
        self.write(
            "User-agent: *\nAllow: /\nSitemap: "
            + education_public.PRODUCTION_ORIGIN.rstrip("/")
            + "/sitemap.xml\n"
        )


def install_routes(application):
    """Tornado inserts host handlers before its existing wildcard fallback."""
    application.add_handlers(r".*", [
        (r"/education/?", EducationPageHandler, {"page_key": "education"}),
        (r"/education/ai-ed-shorts/?", EducationPageHandler, {"page_key": "ai-ed-shorts"}),
        (r"/privacy/ai-ed-shorts/?", EducationPageHandler, {"page_key": "privacy"}),
        (r"/education-assets/(stock-sentinel-tokens\.css|stock-sentinel-components\.css|education\.css)", EducationAssetHandler),
        (r"/sitemap\.xml", EducationSitemapHandler),
        (r"/robots\.txt", EducationRobotsHandler),
    ])
    return application
