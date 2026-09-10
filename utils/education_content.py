"""Shared, side-effect-free copy for the public Education pages.

Both public page renderers use this content so the application identity and policy
stay consistent. Paths are supplied by each renderer; this module does not know
about authentication, deployment hosts, or application credentials.
"""
from html import escape
from typing import Mapping

PAGE_TITLES = {
    "education": "Education | The Stock Sentinel",
    "ai-ed-shorts": "AI Ed Shorts | The Stock Sentinel",
    "privacy": "AI Ed Shorts Publisher Privacy Policy | The Stock Sentinel",
}
PAGE_DESCRIPTIONS = {
    "education": "Learn about markets, investing, artificial intelligence, large language models, machine learning, and emerging technology with The Stock Sentinel Education.",
    "ai-ed-shorts": "Short visual lessons covering artificial intelligence, large language models, machine learning, AI engineering, and related technical concepts.",
    "privacy": "How AI Ed Shorts Publisher uses Google and YouTube authorization, publishing information, and supporting services for its educational publishing workflow.",
}
DEFAULT_PATHS = {
    "home": "/Home",
    "education": "/education",
    "ai-ed-shorts": "/education/ai-ed-shorts",
    "privacy": "/privacy/ai-ed-shorts",
    "contact": "/Contact",
    "terms": "https://about.thestocksentinel.com/terms/",
}


def page_body(page: str, paths: Mapping[str, str] | None = None) -> str:
    """Return the approved page body, with escaped deployment-specific links.

    Unknown page names raise KeyError rather than silently serving another policy.
    Header, footer, metadata and page-level layout belong to the calling renderer.
    """
    if page not in PAGE_TITLES:
        raise KeyError(page)
    urls = {key: escape(value, quote=True) for key, value in {**DEFAULT_PATHS, **(paths or {})}.items()}
    bodies = {
        "education": _education,
        "ai-ed-shorts": _publisher,
        "privacy": _privacy,
    }
    return '<div class="ed-content">' + bodies[page](urls) + '</div>'


def _education(u):
    return f'''
<nav class="ed-crumbs" aria-label="Breadcrumb"><a href="{u['home']}">Home</a><span aria-hidden="true">›</span><span aria-current="page">Education</span></nav>
<div class="ed-eyebrow">Stock Sentinel Education</div>
<h1>Education</h1>
<p class="ed-lead">Learn the concepts behind markets, technology, and intelligent systems.</p>
<p>The Stock Sentinel Education hub brings together practical learning resources covering markets, investing, artificial intelligence, machine learning, and emerging technology.</p>
<div class="ed-grid">
<article class="ed-card"><div class="ed-eyebrow">Markets</div><h2>Markets &amp; Investing</h2>
<p>Learn the concepts behind market structure, investing, technical signals, risk, portfolio behavior, and the forces that move financial markets.</p>
<p class="ed-meta ed-quiet">This category has no published lessons yet.</p></article>
<article class="ed-card ed-feature"><div class="ed-eyebrow">Technology</div><h2>AI &amp; Technology</h2>
<p>Explore artificial intelligence, large language models, machine learning, AI infrastructure, inference, training, retrieval systems, agents, and other emerging technologies.</p>
<div class="ed-subcard"><h3>AI Ed Shorts</h3><p>Short visual lessons designed to explain complex AI and machine-learning concepts clearly and quickly.</p>
<a class="ed-button ed-primary" href="{u['ai-ed-shorts']}">Explore AI Ed Shorts</a></div></article>
</div>'''


def _publisher(u):
    topics = (
        "Large Language Models", "Retrieval-Augmented Generation", "AI Agents",
        "Transformer Architecture", "Model Training", "Inference Optimization",
        "GPU & AI Infrastructure", "Model Evaluation", "Prompt Engineering",
        "Vector Search", "Quantization", "Attention Mechanisms",
    )
    topic_html = ''.join(f'<li>{escape(topic)}</li>' for topic in topics)
    return f'''
<nav class="ed-crumbs" aria-label="Breadcrumb"><a href="{u['home']}">Home</a><span aria-hidden="true">›</span><a href="{u['education']}">Education</a><span aria-hidden="true">›</span><span>AI &amp; Technology</span><span aria-hidden="true">›</span><span aria-current="page">AI Ed Shorts</span></nav>
<div class="ed-eyebrow">AI &amp; Technology</div><h1>AI Ed Shorts</h1>
<p class="ed-lead">Short visual lessons for understanding AI, LLMs, machine learning, and AI engineering.</p>
<p>AI Ed Shorts is an educational content series focused on making complex artificial-intelligence concepts easier to understand through concise visual explanations. Topics may include large language models, machine learning, retrieval-augmented generation, model training, inference, attention mechanisms, AI infrastructure, optimization, evaluation, and related technical concepts.</p>
<a href="{u['privacy']}">AI Ed Shorts Privacy Policy</a>
<section class="ed-section"><h2>About AI Ed Shorts Publisher</h2>
<p>AI Ed Shorts Publisher is a personal content-creation and publishing application used to produce and publish short-form educational videos for the AI Ed Shorts series.</p>
<p>The application generates an educational video together with proposed publishing metadata such as a title, description, and hashtags. The complete content package is reviewed by the account owner before any publishing action occurs.</p></section>
<section class="ed-section" id="workflow"><h2>How publishing works</h2>
<ol class="ed-steps">
<li><span>01 · CONCEPT</span>Educational concept selected</li>
<li><span>02 · VIDEO</span>Short educational video generated</li>
<li><span>03 · METADATA</span>Title, description and hashtags generated</li>
<li class="ed-approval"><span>04 · HUMAN REVIEW</span>Owner reviews the exact video and metadata</li>
<li class="ed-approval"><span>05 · EXPLICIT APPROVAL</span>Owner explicitly approves the package</li>
<li><span>06 · PUBLISH</span>Approved content is published</li>
</ol>
<div class="ss-system-state ed-approval-note"><h3>Human Review Before Publishing</h3>
<p>Publishing is human-reviewed. AI Ed Shorts Publisher does not automatically publish newly generated content without an explicit approval step from the account owner.</p>
<p>Before publishing, the account owner receives the exact video together with its proposed title, description, and hashtags for review.</p>
<p>The owner may approve the package or request changes. Only an approved version is eligible for publishing.</p>
<p>If the video or metadata changes, the updated version must be reviewed again before publication.</p></div></section>
<div class="ed-grid"><section class="ed-card"><h2>YouTube Integration</h2>
<p>AI Ed Shorts Publisher uses the YouTube Data API to upload educational videos to the YouTube channel explicitly authorized by the account owner through Google OAuth.</p>
<p>The application requests YouTube upload access for the purpose of publishing content that the account owner has already reviewed and approved.</p>
<p>The application does not upload content to unrelated YouTube channels and does not publish newly generated content without owner approval.</p></section>
<section class="ed-card"><h2>Current Use</h2>
<p>AI Ed Shorts Publisher is currently operated as a personal, single-user educational publishing workflow. It is not currently offered as a public publishing service for third parties.</p>
<nav class="ed-resource-links" aria-label="Publisher resources">
<a href="{u['privacy']}">AI Ed Shorts Privacy Policy</a>
<a href="{u['contact']}">Contact The Stock Sentinel</a>
<a href="{u['terms']}">Stock Sentinel Terms</a></nav></section></div>
<section class="ed-section"><h2>Topics Covered</h2><ul class="ed-topics">{topic_html}</ul></section>'''


def _privacy(u):
    return f'''
<nav class="ed-crumbs" aria-label="Breadcrumb"><a href="{u['education']}">Education</a><span aria-hidden="true">›</span><a href="{u['ai-ed-shorts']}">AI Ed Shorts</a><span aria-hidden="true">›</span><span aria-current="page">Privacy Policy</span></nav>
<div class="ed-reading"><div class="ed-eyebrow">Dedicated application policy</div>
<h1>AI Ed Shorts Publisher Privacy Policy</h1><p class="ed-meta">Last updated: September 10, 2026</p>
<p>This Privacy Policy describes how AI Ed Shorts Publisher accesses, uses, stores, and manages information required to operate its educational content publishing workflow.</p>
<p>AI Ed Shorts Publisher is currently a personal, single-user application operated by the site owner.</p>
<section><h2>Google and YouTube API Access</h2>
<p>AI Ed Shorts Publisher uses Google OAuth 2.0 and the YouTube Data API to upload educational video content to a YouTube channel explicitly authorized by the account owner.</p>
<p>The application currently requests the following permission:</p><code>https://www.googleapis.com/auth/youtube.upload</code>
<p>This permission is used only to upload content that has been reviewed and approved by the authorized account owner. The application does not use this authorization to access unrelated Google services.</p></section>
<section><h2>Authorization Credentials</h2>
<p>Google OAuth authorization credentials may be stored securely so the application can maintain authorized access and refresh authentication without requiring the account owner to sign in before every approved upload.</p>
<p>These credentials are treated as sensitive information and are not exposed publicly.</p></section>
<section><h2>Publishing Information</h2><p>The application may store:</p><ul>
<li>Educational content identifiers</li><li>Generated video identifiers</li><li>Title</li><li>Description</li><li>Hashtags</li><li>Content revision/version</li><li>Approval state</li><li>Publishing status</li><li>YouTube video identifier</li><li>Publication timestamp</li><li>Platform status</li></ul>
<p>This information is used to manage the publishing workflow, maintain revision history, prevent duplicate uploads, and track whether content has already been published.</p></section>
<section><h2>How Information Is Used</h2><p>Information is used only to:</p><ul>
<li>Authenticate authorized publishing access</li><li>Prepare approved educational content for publication</li><li>Publish approved content</li><li>Track publishing state</li><li>Prevent duplicate publication</li><li>Maintain operational records for the application</li></ul>
<p>AI Ed Shorts Publisher does not sell Google user data. Google user data obtained through OAuth is not used to create advertising profiles or for targeted advertising.</p></section>
<section><h2>Third-Party Services</h2><p>Supporting services may be used for AI content generation, private media storage, approval messaging, social-media publishing, and application infrastructure.</p>
<p>Content and publishing information may be shared with supporting services as needed to perform these functions. The use of these services does not imply that their providers sponsor or endorse the application.</p></section>
<section><h2>Data Retention</h2><p>Authorization credentials and publishing records are retained only as needed to operate the publishing workflow, maintain publishing history, prevent duplicate uploads, and support the application's authorized functionality.</p>
<p>Records may be removed when they are no longer required, when authorization is revoked, or when the application is discontinued.</p></section>
<section><h2>Revoking Google Access</h2><p>The account owner may revoke AI Ed Shorts Publisher's access to Google services at any time through their <a href="https://myaccount.google.com/permissions">Google Account permissions</a>.</p></section>
<section><h2>Contact</h2><p>For questions about AI Ed Shorts Publisher or this privacy policy, use our existing contact form.</p><p><a href="{u['contact']}">Contact The Stock Sentinel</a></p></section>
<p class="ed-quiet"><a href="{u['ai-ed-shorts']}"><span aria-hidden="true">←</span> Back to AI Ed Shorts</a></p></div>'''
