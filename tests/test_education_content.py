#!/usr/bin/env python3
"""Public copy, native previews and shared footer contracts; no paid services."""
from pathlib import Path
from html.parser import HTMLParser
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.education_content import PAGE_TITLES, page_body
from utils.education_public import NATIVE_PATHS, NATIVE_PAGES, render_document


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(); self.h1 = 0; self.hrefs = []; self.anchors = []; self._anchor = None; self.text = []; self.feed(source)
    def handle_starttag(self, tag, attrs):
        if tag == 'h1': self.h1 += 1
        if tag == 'a':
            self.hrefs.append(dict(attrs).get('href'))
            self._anchor = [dict(attrs).get('href'), '']
    def handle_data(self, data):
        self.text.append(data)
        if self._anchor is not None: self._anchor[1] += data
    def handle_endtag(self, tag):
        if tag == 'a' and self._anchor is not None:
            self.anchors.append(tuple(self._anchor)); self._anchor = None


class EducationTests(unittest.TestCase):
    def test_shared_content_public_identity_and_contact(self):
        for key in PAGE_TITLES:
            text = page_body(key)
            doc = Document(text)
            self.assertEqual(doc.h1, 1)
            self.assertNotIn('[CONTACT_EMAIL]', text)
            self.assertNotIn('mailto:', text)
            self.assertNotIn('NVIDIA', text)
            self.assertTrue(all(doc.hrefs))
        publisher = page_body('ai-ed-shorts')
        for fact in ('AI Ed Shorts Publisher','YouTube Data API','Google OAuth',
                     'single-user','Owner explicitly approves','video and metadata',
                     '/privacy/ai-ed-shorts','/Contact'):
            self.assertIn(fact,publisher)
        privacy = page_body('privacy')
        self.assertIn('https://www.googleapis.com/auth/youtube.upload', privacy)
        self.assertIn('https://myaccount.google.com/permissions',privacy)
        self.assertIn('Contact The Stock Sentinel',privacy)

    def test_production_metadata_and_native_links(self):
        for key in PAGE_TITLES:
            self.assertIn('<title>'+PAGE_TITLES[key]+'</title>',render_document(key))
            doc = Document(page_body(key,NATIVE_PATHS))
            self.assertTrue(all(not u.startswith('/education') and not u.startswith('/privacy/ai-ed-shorts')
                                for u in doc.hrefs))
        with self.assertRaises(KeyError): page_body('missing')
        self.assertIn('&quot;',page_body('education',{'ai-ed-shorts':'/"unsafe'}))

    def test_native_pages_are_public_without_network(self):
        from streamlit.testing.v1 import AppTest
        import streamlit as st
        def prohibit(*args,**kwargs): raise AssertionError('Network forbidden')
        for key,path in NATIVE_PAGES.items():
            links=[]
            with patch.object(st,'page_link',lambda page,**kw:links.append((page,kw.get('label')))), \
                 patch('requests.sessions.Session.request',prohibit), patch('httpx.Client.send',prohibit):
                app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/path)).run()
                self.assertFalse(list(app.exception),str(list(app.exception)))
                self.assertTrue(any(label=='Education' for _,label in links))
                self.assertTrue(any(label=='Privacy' for _,label in links))
                self.assertTrue(any(label=='Terms' for _,label in links))
                html=''.join(node.proto.body for node in app.get('html'))
                # Content rendering alone missed the Cloud iframe regression. Every
                # internal content link must now be a native page_link widget.
                expected = Document(page_body(key,NATIVE_PATHS))
                for href,label in expected.anchors:
                    destination = ('pages/' + href.lstrip('/') + '.py'
                                   if href.startswith('/') else href)
                    label = label.replace('←', '').strip()
                    self.assertIn((destination,label),links)
                self.assertFalse(Document(html).hrefs,
                                 'Content links must use native navigation, including external links')
                self.assertEqual(Document(html).h1,1)
                # Shared prose remains rendered even though links are widgets.
                rendered = ' '.join(Document(html).text)
                for phrase in ('AI Ed Shorts',):
                    self.assertIn(phrase,rendered)

    def test_unmapped_native_link_fails_before_rendering(self):
        from utils.education_native import render_native_body
        import streamlit as st
        with patch.object(st, 'html') as paint:
            with self.assertRaisesRegex(ValueError, 'no native destination'):
                render_native_body('<p>Intro</p><a href="/Education/ai-ed-shorts">Open</a>', {})
            paint.assert_not_called()

    def test_generated_and_existing_static_footers_keep_education(self):
        root=Path(__file__).resolve().parents[1]
        for path in (root/'site').rglob('*.html'):
            source=path.read_text()
            if '<footer' in source:
                self.assertIn('https://thestocksentinel.com/education',source.split('<footer',1)[1],str(path))
        from scripts.generate_pulse_pages import FOOT
        self.assertIn('https://thestocksentinel.com/education',FOOT)
        terms=(root/'site/terms/index.html').read_text()
        self.assertIn('AI Ed Shorts',terms)
        self.assertIn('https://www.youtube.com/t/terms',terms)


if __name__=='__main__': unittest.main()
