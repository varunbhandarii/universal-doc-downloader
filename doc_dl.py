import logging
import sys
import argparse
from datetime import datetime
from urllib.parse import urljoin, urldefrag, urlparse
from tqdm import tqdm

from bs4 import BeautifulSoup
import weasyprint
from playwright.sync_api import sync_playwright

# --- CONFIGURATION & PRESETS ---
SITE_PRESETS = {
    "flask.palletsprojects.com": "div.sphinxsidebar",
    "react.dev": "nav[aria-label='Main']",
    "playwright.dev": ".menu__list",
    "docs.python.org": "div.sphinxsidebar",
    "readthedocs.io": "div.sphinxsidebar",
    "django": "#docs-content",
    # Mintlify-hosted sites
    "mintlify.app": "aside",
    "explore.airia.com": "aside",
    # Material for MkDocs
    "airiallc.github.io": ".md-nav--primary",
}

DEFAULT_SELECTOR = "div.sphinxsidebar"

CONTENT_SELECTORS = [
    "[data-component-name='Layout/DocumentationLayout']",
    ".redoc-wrap",
    ".api-content",
    "div.document",
    "div[itemprop='articleBody']",
    "div.body",
    ".prose",           # Mintlify
    ".md-content__inner",  # Material for MkDocs
    ".md-typeset",         # Material for MkDocs (fallback)
    "article",
    "section",
    "main",
    ".markdown-section",
    ".theme-doc-markdown"
]

MINTLIFY_DOMAINS = ["mintlify.app", "explore.airia.com"]

CSS_STYLES = """
    @page { 
        size: A4; margin: 1.5cm; margin-bottom: 2.5cm; 
        @bottom-center {
            content: "Page " counter(page);
            font-family: sans-serif; font-size: 9pt; color: #7f8c8d;
            border-top: 1px solid #eee; padding-top: 10px; width: 100%;
        }
    }
    @page cover { margin: 0; @bottom-center { content: none; } }
    
    * { box-sizing: border-box; max-width: 100% !important; }
    body { font-family: sans-serif; font-size: 10pt; line-height: 1.6; color: #333; text-align: justify; }

    img { max-width: 100% !important; height: auto !important; display: block; margin: 1.5em auto; border: 1px solid #e1e4e8; page-break-inside: avoid; }
    pre { background: #f6f8fa; padding: 12px; border: 1px solid #e1e4e8; border-radius: 4px; font-family: monospace; font-size: 8.5pt; white-space: pre-wrap !important; word-break: break-all !important; page-break-inside: avoid; }
    
    h1 { border-bottom: 2px solid #2c3e50; padding-bottom: 5px; margin-top: 0; color: #2c3e50; page-break-after: avoid; }
    h2 { border-bottom: 1px solid #eee; margin-top: 2em; color: #e67e22; page-break-after: avoid; }
    
    .cover-page { 
        page: cover; 
        text-align: center; 
        padding-top: 35%; 
        height: 100%; 
        page-break-after: always;
    }
    .cover-title { font-size: 36pt; font-weight: bold; color: #2c3e50; border-bottom: 4px solid #2c3e50; display: inline-block; margin-bottom: 20px;}
    .cover-subtitle { font-size: 18pt; color: #7f8c8d; }
    .cover-footer { font-size: 10pt; color: #95a5a6; margin-top: 100px; }

    .toc-page { 
        page-break-before: always; 
        page-break-after: always; 
        padding: 2em; 
    }
    
    .toc-header { text-align: center; font-size: 24pt; margin-bottom: 2em; border-bottom: 2px solid #eee; }
    ul.toc-list { list-style-type: none; padding: 0; }
    li.toc-entry { margin-bottom: 0.5em; }
    a.toc-link { text-decoration: none; color: #333; display: block; }
    a.toc-link::after { content: leader('.') target-counter(attr(href), page); float: right; color: #7f8c8d; }
    
    a.internal-link { color: #2980b9; text-decoration: none; border-bottom: 1px dotted #2980b9; }
    
    .admonition { padding: 12px; margin-bottom: 15px; border: 1px solid #eee; border-left-width: 5px; border-radius: 4px; page-break-inside: avoid; }
    .admonition-title { font-weight: bold; display: block; margin-bottom: 5px; text-transform: uppercase; font-size: 0.9em; }
    .note { background-color: #e7f2fa; border-left-color: #6ab0de; }
    .warning { background-color: #fff3cd; border-left-color: #ffc107; }

    table { width: 100% !important; border-collapse: collapse; margin-bottom: 1.5em; font-size: 9pt; table-layout: fixed; }
    td, th { border: 1px solid #ddd; padding: 8px; word-wrap: break-word; word-break: break-word; vertical-align: top; }
    th { background-color: #f8f9fa; font-weight: bold; color: #2c3e50; }
    tr { page-break-inside: avoid; }
"""

def generate_cover_html(title, subtitle):
    date_str = datetime.now().strftime("%B %d, %Y")
    return f"""
    <div class="cover-page">
        <div class="cover-title">{title}</div>
        <div class="cover-subtitle">{subtitle}</div>
        <div class="cover-footer">Generated on: {date_str}</div>
    </div>
    """

def generate_toc_html(chapters):
    toc_items = ""
    for chap in chapters:
        toc_items += f"""<li class="toc-entry"><a class="toc-link" href="#{chap['id']}">{chap['title']}</a></li>"""
    return f"""<div class="toc-page"><div class="toc-header">Table of Contents</div><ul class="toc-list">{toc_items}</ul></div>"""

def sanitize_content(soup_element):
    for selector in [
        "a.headerlink", ".toctree-wrapper", ".rst-footer-buttons", ".wy-breadcrumbs", 
        "div[role='navigation']", "script", "style", ".exclude-print", "nav", 
        "button", "div[class*='PageActions']", "div[class*='PageNavigation']", "div[class*='Feedback']"
    ]:
        for tag in soup_element.select(selector): tag.decompose()

    SAFE_CLASSES = ['admonition', 'note', 'warning', 'tip', 'attention', 'caution', 'danger', 'error', 'admonition-title']
    allowed_attrs = ['src', 'href', 'colspan', 'rowspan', 'id', 'class']

    for tag in soup_element.find_all(True):
        new_attrs = {}
        for k, v in tag.attrs.items():
            if k in allowed_attrs: new_attrs[k] = v
        if 'class' in tag.attrs:
            kept = [c for c in tag.attrs['class'] if c in SAFE_CLASSES]
            if kept: new_attrs['class'] = kept
        tag.attrs = new_attrs
    return soup_element

def repair_links(soup, current_page_url, url_map):
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href or href.startswith("#"): continue
        absolute_url = urljoin(current_page_url, href)
        clean_url, _ = urldefrag(absolute_url)
        if clean_url in url_map:
            target_id = url_map[clean_url]
            a['href'] = f"#{target_id}"
            a['class'] = a.get('class', []) + ['internal-link']
    return soup

def detect_selector(url, user_selector):
    if user_selector and user_selector != DEFAULT_SELECTOR:
        return user_selector
    for domain, selector in SITE_PRESETS.items():
        if domain in url:
            print(f"✨ Auto-detected preset for {domain}!")
            return selector
    return DEFAULT_SELECTOR

# --- SPIDER ---
def is_mintlify(url):
    return any(domain in url for domain in MINTLIFY_DOMAINS)

def get_dynamic_links(page, start_url, sidebar_selector):
    print(f"🕷️  Spider: Navigating to {start_url}")
    try:
        page.goto(start_url, timeout=60000)
        # Mintlify/Next.js sites need full network idle to hydrate nav
        if is_mintlify(start_url):
            page.wait_for_load_state("networkidle", timeout=30000)
        else:
            page.wait_for_load_state("domcontentloaded")

        actual_url = page.url
        if actual_url != start_url:
            print(f"   🔄 Redirected to: {actual_url}")

        clean_actual = actual_url.split('?')[0].split('#')[0]
        parsed = urlparse(clean_actual)
        path_parts = [p for p in parsed.path.split('/') if p]

        if 'github.io' in parsed.netloc:
            # GitHub Pages: site root is scheme://netloc/repo-name
            root_path = '/' + path_parts[0] if path_parts else ''
            base_prefix = f"{parsed.scheme}://{parsed.netloc}{root_path}"
        elif is_mintlify(start_url):
            # Mintlify sidebar links are siblings, one level up from start URL
            parent_path = '/'.join(parsed.path.rstrip('/').split('/')[:-1])
            base_prefix = f"{parsed.scheme}://{parsed.netloc}{parent_path}"
        else:
            base_prefix = clean_actual
        if base_prefix.endswith('/'): base_prefix = base_prefix[:-1]
        print(f"   📌 Base prefix: {base_prefix}")

        print("   🔓 Attempting to expand sidebar menus...")
        try:
            if is_mintlify(start_url):
                # Mintlify uses <button> elements to toggle collapsible nav groups
                page.evaluate(f"""() => {{
                    const sidebar = document.querySelector('{sidebar_selector}') || document.querySelector('aside');
                    if (!sidebar) return;
                    const buttons = sidebar.querySelectorAll('button');
                    buttons.forEach(btn => {{ btn.click(); }});
                }}""")
                page.wait_for_timeout(1500)
            else:
                page.evaluate(f"""() => {{
                    const sidebar = document.querySelector('{sidebar_selector}') || document.querySelector('aside') || document.querySelector('nav');
                    if (!sidebar) return;
                    const groups = sidebar.querySelectorAll('li[role="link"], .menu-item-type-group');
                    groups.forEach(group => {{ group.click(); }});
                }}""")
                page.wait_for_timeout(1000)
        except Exception as e:
            print(f"   ⚠️ Expander warning: {e}")

        clean_links = []
        seen = set()

        def process_links(raw_list):
            count = 0
            for link in raw_list:
                clean_link, _ = urldefrag(link)
                if not clean_link.startswith(base_prefix): continue
                if clean_link == actual_url or clean_link == base_prefix or clean_link == base_prefix + "/": continue
                if clean_link not in seen:
                    clean_links.append(clean_link)
                    seen.add(clean_link)
                    count += 1
            return count

        print(f"   🔎 Strategy A: Sidebar ({sidebar_selector})...")
        try:
            raw_a = page.evaluate(f"""() => {{
                const el = document.querySelector('{sidebar_selector}') || document.querySelector('aside') || document.querySelector('nav');
                return el ? Array.from(el.querySelectorAll('a')).map(a => a.href) : [];
            }}""")
            added = process_links(raw_a)
            print(f"      Found {len(raw_a)} raw, {added} new valid links.")
        except: pass

        if len(clean_links) < 3:
            print("   🔎 Strategy B: TOC Wrapper...")
            try:
                raw_b = page.evaluate("() => Array.from(document.querySelectorAll('.toctree-wrapper a')).map(a => a.href)")
                process_links(raw_b)
            except: pass

        if len(clean_links) < 3:
            print("   ☢️  Strategy C: Nuclear...")
            try:
                raw_c = page.evaluate("""() => {
                    const el = document.querySelector('main') || document.querySelector('div.body') || document.body;
                    return Array.from(el.querySelectorAll('a')).map(a => a.href);
                }""")
                process_links(raw_c)
            except: pass
        
        return clean_links

    except Exception as e:
        print(f"❌ Spider Error: {e}")
        return []

def fetch_page_content(page, url, chapter_id, url_map):
    try:
        page.goto(url, timeout=60000)
        if is_mintlify(url):
            page.wait_for_load_state("networkidle", timeout=30000)
        else:
            page.wait_for_load_state("domcontentloaded")
            # Wait for API content to hydrate
            page.wait_for_timeout(2000)

        soup = BeautifulSoup(page.content(), "html.parser")
        
        content = None
        for selector in CONTENT_SELECTORS:
            content = soup.select_one(selector)
            if content: break
        
        if not content: return None, None

        for img in content.find_all("img"):
            if img.get("src"): img['src'] = urljoin(url, img['src'])

        content = sanitize_content(content)
        content = repair_links(content, url, url_map)

        h1 = content.find("h1")
        title = "Untitled"
        
        if h1:
            title = h1.get_text(strip=True)
            h1['id'] = chapter_id
        else:
            h2 = content.find("h2")
            if h2:
                title = h2.get_text(strip=True)
            else:
                try:
                    title = page.title().split('|')[0].strip()
                except:
                    pass
            
            new_tag = soup.new_tag("div", id=chapter_id)
            content.insert(0, new_tag)

        return str(content), title
    except Exception as e:
        return None, None

def create_pdf(html_content, filename, doc_title):
    print(f"\n📄 Rendering PDF: {filename}...")
    full_doc = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{doc_title}</title>
        <style>{CSS_STYLES}</style>
    </head>
    <body>{html_content}</body>
    </html>
    """
    try:
        logger = logging.getLogger('weasyprint')
        logger.setLevel(logging.ERROR)
        weasyprint.HTML(string=full_doc).write_pdf(filename)
        print(f"✅ Success! Saved to {filename}")
    except Exception as e:
        print(f"❌ PDF Error: {e}")

def setup_arg_parser():
    parser = argparse.ArgumentParser(description="Universal Doc Downloader")
    parser.add_argument("url", help="The URL of the documentation homepage.")
    parser.add_argument("--selector", "-s", default=DEFAULT_SELECTOR, help="CSS selector for the sidebar.")
    parser.add_argument("--output", "-o", default="manual.pdf", help="Output filename.")
    parser.add_argument("--title", "-t", default="Documentation", help="Title for the cover page")
    parser.add_argument("--limit", "-l", type=int, default=0, help="Limit pages (0 = all)")
    parser.add_argument("--visible", action="store_true", help="Show browser window")
    return parser

def main(args):
    print(f"--- 🚀 Universal Doc Downloader ---")
    print(f"Target: {args.url}")
    
    final_selector = detect_selector(args.url, args.selector)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.visible)
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})

        links = get_dynamic_links(page, args.url, final_selector)
        
        if not links:
            print("❌ No links found.")
            browser.close()
            return
        
        print(f"✅ Found {len(links)} chapters.")
        if args.limit > 0:
            links = links[:args.limit]

        url_map = {link: f"chap_{i}" for i, link in enumerate(links)}
        chapters_data = []
        all_html = ""
        
        print("--- Downloading Content ---")
        for i, link in tqdm(enumerate(links), total=len(links), unit="page"):
            chap_id = url_map[link]
            content, title = fetch_page_content(page, link, chap_id, url_map)
            if content:
                chapters_data.append({'id': chap_id, 'title': title})
                all_html += '<div style="page-break-before: always;"></div>' + content
        
        browser.close()

    if chapters_data:
        print("--- Assembling Document ---")
        cover = generate_cover_html(args.title, args.url)
        toc = generate_toc_html(chapters_data)
        final_doc = cover + toc + all_html
        create_pdf(final_doc, args.output, args.title)
    else:
        print("❌ Aborted.")

if __name__ == "__main__":
    parser = setup_arg_parser()
    args = parser.parse_args()
    main(args)