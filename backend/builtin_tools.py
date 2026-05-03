"""
Built-in tools — always available to every agent, no configuration required.

Currently provides:
  - web_search   : Tavily Search API (requires TAVILY_API_KEY in .env)
  - fetch_url    : HTTP GET/POST with response text extraction
  - read_export_file : Read prior workflow exports from backend/exports
  - export_artifact : Export agent content to local files
  - obsidian_export_pdf : High-quality Markdown to PDF via obsidian-export
  - presenton_generate_pptx : Optional PPTX via Presenton self-host/API
"""

import asyncio
import json
from pathlib import Path
import re
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

BUILTIN_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information. Returns a list of results with "
                "titles, snippets, and URLs. Use this when you need up-to-date facts, "
                "news, documentation, or any information not in your training data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default 8, max 20).",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch the content of any URL and return the page text. Useful for reading "
                "articles, documentation, GitHub files, or any public web page. "
                "Automatically strips HTML tags and extracts readable text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Truncate response to this many characters (default 8000).",
                        "default": 8000,
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_export_file",
            "description": (
                "Read a previous workflow export from the local backend exports folder. "
                "Use this when the user references a saved Deep Analyst Markdown report "
                "by filename/path and wants Report Producer to create deliverables from it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Export filename or path under backend/exports, e.g. Deep-Analyst-Research-Report.md.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Truncate response to this many characters (default 120000).",
                        "default": 120000,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_artifact",
            "description": (
                "Create a local export file from polished, structured agent content. Use this when "
                "the user asks for an actual Markdown, HTML, DOCX, PPTX, XLSX, CSV, or PDF "
                "deliverable. For PDF and PPTX, provide concise sections/slides instead of one "
                "large markdown blob. A PPTX deck must be a logically clear, visually presentable "
                "summary of the document, not copied report paragraphs. Returns the generated file "
                "path and a download URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Human-readable title for the exported artifact.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["markdown", "html", "docx", "pptx", "xlsx", "csv", "pdf"],
                        "description": "Output file format.",
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "Optional safe filename. Extension may be omitted. Include the authoring "
                            "agent name when known, e.g. Deep-Analyst-..., Report-Producer-..., "
                            "or Research-Agent-...."
                        ),
                    },
                    "subtitle": {
                        "type": "string",
                        "description": "Optional subtitle, date line, audience, or one-sentence framing statement.",
                    },
                    "theme": {
                        "type": "string",
                        "enum": ["consulting", "clinical", "technical", "executive"],
                        "description": "Optional visual style hint for PDF/PPTX exports.",
                    },
                    "content_markdown": {
                        "type": "string",
                        "description": "Main body content as Markdown. Prefer sections/slides for long PDF/PPTX exports.",
                    },
                    "sections": {
                        "type": "array",
                        "description": "Report or memo sections. Use 4-10 concise sections for readable PDF reports.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "body": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "slides": {
                        "type": "array",
                        "description": (
                            "Slide deck content. Use the requested number of concise slides. "
                            "Synthesize the report into a clear presentation storyline; do not "
                            "copy/paste PDF/report paragraphs."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "subtitle": {"type": "string"},
                                "purpose": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "takeaway": {"type": "string"},
                                "visual": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                        },
                    },
                    "tables": {
                        "type": "array",
                        "description": "Tables or worksheets. Use for XLSX/CSV exports.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "columns": {"type": "array", "items": {"type": "string"}},
                                "rows": {
                                    "type": "array",
                                    "items": {
                                        "oneOf": [
                                            {"type": "array", "items": {}},
                                            {"type": "object"},
                                        ]
                                    },
                                },
                            },
                        },
                    },
                },
                "required": ["title", "format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obsidian_export_pdf",
            "description": (
                "Render Obsidian-friendly Markdown into a higher-quality PDF using the local "
                "obsidian-export CLI. Prefer this over export_artifact for final PDF reports. "
                "Requires obsidian-export plus its system dependencies; returns a clear setup "
                "error if the renderer is not available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Human-readable report title.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Safe output filename without extension. Include the authoring agent name, e.g. Report-Producer-Research-Report.",
                    },
                    "content_markdown": {
                        "type": "string",
                        "description": "Obsidian-friendly Markdown content to render to PDF.",
                    },
                    "input_path": {
                        "type": "string",
                        "description": "Optional path to an existing Markdown file to render instead of content_markdown.",
                    },
                    "profile": {
                        "type": "string",
                        "description": "Optional obsidian-export profile name.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Render timeout in seconds. Default 180.",
                        "default": 180,
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "presenton_generate_pptx",
            "description": (
                "Generate a high-quality editable presentation deck using a healthy self-hosted "
                "Presenton API. Use this only when Presenton is explicitly enabled and working; "
                "otherwise use export_artifact for local PPTX generation. "
                "Use a separate slide narrative with one purpose per slide, not copied PDF text. "
                "Keep slide text short enough to prevent overflow: one-line titles, max 4 bullets, "
                "and a separate short takeaway area that does not collide with bullet text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Presentation title.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Safe output filename without extension. Include Report-Producer, e.g. Report-Producer-Presentation-Deck.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Presentation brief or synthesized source content for Presenton.",
                    },
                    "content_markdown": {
                        "type": "string",
                        "description": "Markdown source content for Presenton.",
                    },
                    "slides": {
                        "type": "array",
                        "description": "Optional explicit slide narrative; each slide should be synthesized, concise, and presentation-ready. Keep titles <=58 chars, bullets <=4 items and <=105 chars each, and takeaway <=95 chars.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "subtitle": {"type": "string"},
                                "purpose": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "takeaway": {"type": "string"},
                                "visual": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                        },
                    },
                    "n_slides": {
                        "type": "integer",
                        "description": "Number of slides to generate. Default 8.",
                        "default": 8,
                    },
                    "template": {
                        "type": "string",
                        "description": "Presenton template name. Default general.",
                        "default": "general",
                    },
                    "tone": {
                        "type": "string",
                        "enum": ["default", "casual", "professional", "funny", "educational", "sales_pitch"],
                        "description": "Presenton tone. Default professional.",
                        "default": "professional",
                    },
                    "verbosity": {
                        "type": "string",
                        "enum": ["concise", "standard", "text-heavy"],
                        "description": "Slide text density. Default concise.",
                        "default": "concise",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Additional deck-generation instructions.",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Presenton API base URL. Defaults to PRESENTON_BASE_URL or http://localhost:5000.",
                    },
                    "api_key": {
                        "type": "string",
                        "description": "Optional Presenton Cloud API key. Prefer PRESENTON_API_KEY environment variable.",
                    },
                    "username": {
                        "type": "string",
                        "description": "Optional self-hosted Presenton admin username. Prefer PRESENTON_USERNAME environment variable.",
                    },
                    "password": {
                        "type": "string",
                        "description": "Optional self-hosted Presenton admin password. Prefer PRESENTON_PASSWORD environment variable.",
                    },
                    "export_as": {
                        "type": "string",
                        "enum": ["pptx", "pdf"],
                        "description": "Export format. Default pptx.",
                        "default": "pptx",
                    },
                },
                "required": ["title"],
            },
        },
    },
]

BUILTIN_TOOL_NAMES = {s["function"]["name"] for s in BUILTIN_TOOL_SCHEMAS}


def is_builtin_tool(tool_name: str) -> bool:
    return tool_name in BUILTIN_TOOL_NAMES


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Strip HTML tags and extract visible text."""

    SKIP_TAGS = {"script", "style", "noscript", "head", "meta", "link"}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


def _strip_html(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = " ".join(parser.parts)
    # Collapse excessive whitespace
    text = re.sub(r"\s{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# web_search — Tavily Search API
# ---------------------------------------------------------------------------

async def _web_search(query: str, max_results: int = 8) -> str:
    import httpx
    import os

    max_results = min(max(1, max_results), 20)

    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return json.dumps({
            "query": query,
            "results": [],
            "note": "TAVILY_API_KEY not set. Add it to backend/.env to enable web search.",
        })

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return json.dumps({"query": query, "results": [], "note": f"Search failed: {e}"})

    results = [
        {
            "title": r.get("title", ""),
            "snippet": r.get("content", ""),
            "url": r.get("url", ""),
        }
        for r in data.get("results", [])
    ]

    if not results:
        return json.dumps({"query": query, "results": [], "note": "No results found."})

    return json.dumps({"query": query, "results": results})


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------

def _rewrite_github_url(url: str) -> tuple[str, str | None]:
    """
    Rewrite a github.com URL to use the GitHub REST API.
    Returns (api_url, readme_url_or_none).
    - github.com/owner/repo            → api.github.com/repos/owner/repo  + readme
    - github.com/owner/repo/blob/…/file → raw.githubusercontent.com/…/file
    - anything else                    → unchanged
    """
    m = re.match(
        r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url
    )
    if m:
        owner, repo = m.group(1), m.group(2)
        return f"https://api.github.com/repos/{owner}/{repo}", \
               f"https://api.github.com/repos/{owner}/{repo}/readme"

    m = re.match(
        r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/blob/(.+)", url
    )
    if m:
        owner, repo, path = m.group(1), m.group(2), m.group(3)
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{path}", None

    return url, None


async def _fetch_url(url: str, max_chars: int = 8000) -> str:
    max_chars = min(max(500, max_chars), 50000)

    import httpx
    import base64

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    rewritten_url, readme_url = _rewrite_github_url(url)
    is_github_repo = readme_url is not None

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=20.0, headers=headers
        ) as client:
            resp = await client.get(rewritten_url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            raw = resp.text

            # For GitHub repo API: also fetch README
            readme_text = ""
            if is_github_repo and readme_url:
                try:
                    readme_resp = await client.get(readme_url)
                    if readme_resp.status_code == 200:
                        readme_data = readme_resp.json()
                        encoded = readme_data.get("content", "")
                        readme_text = base64.b64decode(encoded).decode("utf-8", errors="replace")
                except Exception:
                    pass
    except Exception as e:
        return json.dumps({"error": f"Fetch failed: {e}", "url": url})

    # Extract readable text from HTML; pass JSON/plain text through
    if "html" in content_type:
        text = _strip_html(raw)
    elif "json" in content_type:
        try:
            text = json.dumps(json.loads(raw), indent=2)
        except Exception:
            text = raw
    else:
        text = raw

    if readme_text:
        combined = text + "\n\n--- README ---\n\n" + readme_text
    else:
        combined = text

    if len(combined) > max_chars:
        combined = combined[:max_chars] + f"\n\n[truncated — {len(combined) - max_chars} more characters]"

    return json.dumps({"url": url, "content": combined})


def _read_export_file(path_raw: str, max_chars: int = 120000) -> str:
    exports_dir = (Path(__file__).resolve().parent / "exports").resolve()
    raw = str(path_raw or "").strip().strip("\"'")
    if not raw:
        return json.dumps({"ok": False, "error": "path is required"})

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = exports_dir / candidate
    candidate = candidate.resolve()

    try:
        candidate.relative_to(exports_dir)
    except ValueError:
        return json.dumps({
            "ok": False,
            "error": "read_export_file is restricted to files under backend/exports",
            "exports_dir": str(exports_dir),
        })

    if not candidate.is_file():
        return json.dumps({"ok": False, "error": f"export file not found: {candidate}"})

    if candidate.suffix.lower() not in {".md", ".txt", ".json", ".csv", ".html"}:
        return json.dumps({"ok": False, "error": "read_export_file only reads text export files"})

    text = candidate.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return json.dumps({
        "ok": True,
        "path": str(candidate),
        "filename": candidate.name,
        "content": text,
        "truncated": truncated,
        "size_chars": len(text),
    })


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def execute_builtin_tool(tool_name: str, arguments_str: str) -> str:
    try:
        args = json.loads(arguments_str) if arguments_str else {}
    except json.JSONDecodeError:
        args = {}

    if tool_name == "web_search":
        query = args.get("query", "").strip()
        if not query:
            return json.dumps({"error": "query is required"})
        max_results = int(args.get("max_results", 8))
        return await _web_search(query, max_results)

    if tool_name == "fetch_url":
        url = args.get("url", "").strip()
        if not url:
            return json.dumps({"error": "url is required"})
        max_chars = int(args.get("max_chars", 8000))
        return await _fetch_url(url, max_chars)

    if tool_name == "read_export_file":
        path = args.get("path", "")
        max_chars = int(args.get("max_chars", 120000))
        return _read_export_file(path, max_chars)

    if tool_name == "export_artifact":
        try:
            from export_tools import export_artifact

            result = await asyncio.to_thread(export_artifact, args)
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"Export failed: {e}"})

    if tool_name == "obsidian_export_pdf":
        try:
            from premium_export_tools import obsidian_export_pdf

            result = await asyncio.to_thread(obsidian_export_pdf, args)
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"Obsidian PDF export failed: {e}"})

    if tool_name == "presenton_generate_pptx":
        try:
            from premium_export_tools import presenton_generate_pptx

            result = await presenton_generate_pptx(args)
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"Presenton PPTX generation failed: {e}"})

    return json.dumps({"error": f"Unknown builtin tool: {tool_name}"})
