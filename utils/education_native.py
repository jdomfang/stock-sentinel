"""Render shared Education copy with Streamlit-owned internal navigation.

Community Cloud coordinates its address bar through Streamlit page navigation.
A plain HTML link navigates only its embedded app, so every local page link must
be a real page_link widget. Pure prose remains HTML shared with the public site.
"""
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser


@dataclass
class _Node:
    tag: str
    attrs: list = field(default_factory=list)
    children: list = field(default_factory=list)

    def attr(self, name):
        return dict(self.attrs).get(name, "")

    def html(self):
        attrs = ''.join(f' {k}="{escape(v or "", quote=True)}"' for k, v in self.attrs)
        body = ''.join(child.html() if isinstance(child, _Node) else escape(child)
                       for child in self.children)
        return f'<{self.tag}{attrs}>{body}</{self.tag}>'

    def text(self):
        return ''.join(child.text() if isinstance(child, _Node) else child
                       for child in self.children)


class _Tree(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.feed(source)
        self.close()
        if len(self.stack) != 1:
            raise ValueError("Unclosed Education markup")

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"br", "hr", "img", "input", "meta", "link"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise ValueError(f"Unbalanced Education markup: {tag}")
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def render_native_body(source, destinations):
    """Render trusted shared markup; destinations maps URL paths to page files."""
    import streamlit as st
    counter = 0

    def has_link(node):
        return isinstance(node, _Node) and (
            (node.tag == "a")
            or any(has_link(child) for child in node.children))

    def slot(kind):
        nonlocal counter
        counter += 1
        return f"ed_native_{kind}_{counter}"

    def html(source):
        # A paragraph's terminal period should not become a separate visual row
        # when its final inline link is rendered as a native navigation widget.
        if source.strip() and source.strip() != ".":
            st.html('<div class="ed-content ed-native-fragment">' + source + '</div>')

    def children(nodes):
        # Preserve adjacent prose in one HTML block, including normal paragraph
        # spacing. Split only where supported navigation or layout is required.
        pending = []
        for child in nodes:
            if isinstance(child, _Node) and (has_link(child) or "ed-grid" in child.attr("class").split()):
                html(''.join(pending))
                pending = []
                render(child)
            else:
                pending.append(child.html() if isinstance(child, _Node) else escape(child))
        html(''.join(pending))

    def render(node):
        classes = node.attr("class").split()
        if node.tag == "a":
            href = node.attr("href")
            if href in destinations:
                kind = "primary" if "ed-primary" in classes else "link"
                with st.container(key=slot(kind)):
                    st.page_link(destinations[href], label=node.text().strip().removeprefix("←").strip())
            else:
                with st.container(key=slot("link")):
                    st.page_link(href, label=node.text().strip())
        elif "ed-grid" in classes:
            cards = [child for child in node.children if isinstance(child, _Node)]
            with st.container(key=slot("grid")):
                for column, card in zip(st.columns(len(cards), gap="medium"), cards):
                    with column:
                        render(card)
        elif "ed-crumbs" in classes:
            crumbs = [child for child in node.children if isinstance(child, _Node)]
            with st.container(key=slot("crumbs")):
                for column, crumb in zip(st.columns([max(len(c.text()), 1) for c in crumbs]), crumbs):
                    with column:
                        render(crumb) if crumb.tag == "a" else html(crumb.html())
        else:
            if "ed-content" in classes:
                key = "ed_native_content"
            elif "ed-card" in classes:
                key = slot("feature" if "ed-feature" in classes else "card")
            elif "ed-reading" in classes:
                key = slot("reading")
            elif "ed-resource-links" in classes:
                key = slot("resources")
            elif "ed-subcard" in classes:
                key = slot("subcard")
            elif node.tag == "section":
                key = slot("section")
            elif "ed-quiet" in classes:
                key = slot("quiet")
            else:
                key = slot("group")
            with st.container(key=key):
                children(node.children)

    tree = _Tree(source)

    def validate_links(node):
        if not isinstance(node, _Node):
            return
        href = node.attr("href")
        if node.tag == "a" and href.startswith("/") and href not in destinations:
            raise ValueError(f"Education link has no native destination: {href}")
        for child in node.children:
            validate_links(child)

    validate_links(tree.root)
    for node in tree.root.children:
        if isinstance(node, _Node):
            render(node)
