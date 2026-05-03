"""Higher-quality report/deck export integrations.

These tools intentionally wrap external renderers instead of replacing them:
- obsidian-export for Obsidian-flavored Markdown to PDF
- Presenton self-host/API for AI-generated editable PPTX decks
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from export_tools import EXPORT_DIR, _safe_slug, _unique_export_path


OBSIDIAN_EXPORT_SETUP = (
    "obsidian-export CLI was not found. Install/sync backend dependencies, then ensure "
    "pandoc >= 3.5 and tectonic >= 0.15 are available. Run `uv run obsidian-export doctor` "
    "from the backend folder to verify the renderer."
)


def obsidian_export_pdf(params: dict[str, Any]) -> dict[str, Any]:
    """Render Markdown to PDF through the obsidian-export CLI."""

    title = str(params.get("title") or "Obsidian PDF Export").strip() or "Obsidian PDF Export"
    markdown = str(params.get("content_markdown") or params.get("content") or "").strip()
    input_path_raw = str(params.get("input_path") or "").strip()
    profile = str(params.get("profile") or "").strip()

    if not markdown and not input_path_raw:
        return {"ok": False, "error": "content_markdown or input_path is required"}

    exe = _find_obsidian_export_exe()
    if not exe:
        return {"ok": False, "error": OBSIDIAN_EXPORT_SETUP}

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = EXPORT_DIR / ".obsidian-export-inputs"
    work_dir.mkdir(parents=True, exist_ok=True)

    if input_path_raw:
        source_input_path = Path(input_path_raw).expanduser().resolve()
        if not source_input_path.is_file():
            return {"ok": False, "error": f"input_path does not exist: {source_input_path}"}
        input_path = _flowing_pdf_input_path(work_dir, source_input_path, params.get("filename"), title)
    else:
        input_path = _unique_input_path(work_dir, params.get("filename"), title)
        input_path.write_text(_flowing_pdf_markdown(markdown, title), encoding="utf-8")

    output_path = _unique_export_path(params.get("filename"), title, "pdf")
    cmd = [
        exe,
        "convert",
        "--input",
        str(input_path),
        "--format",
        "pdf",
        "--output",
        str(output_path),
    ]
    if profile:
        cmd.extend(["--profile", profile])

    try:
        env = _export_process_env()
        timeout = int(params.get("timeout_seconds") or 180)
        completed = _run_obsidian_export(cmd, timeout, env)
        if completed.returncode != 0 and profile and _looks_like_missing_profile(completed):
            retry_cmd = [part for part in cmd if part not in {"--profile", profile}]
            completed = _run_obsidian_export(retry_cmd, timeout, env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "obsidian-export timed out while rendering PDF"}
    except Exception as exc:
        return {"ok": False, "error": f"obsidian-export failed to start: {exc}"}

    cleanup_warning = ""
    if completed.returncode != 0 and output_path.is_file() and output_path.stat().st_size > 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if "TemporaryDirectory" in detail and "PermissionError" in detail:
            cleanup_warning = "obsidian-export rendered the PDF but Windows blocked cleanup of its temporary directory."
        else:
            detail = detail[-3000:]
            return {
                "ok": False,
                "error": "obsidian-export PDF render failed",
                "detail": detail,
                "setup_hint": OBSIDIAN_EXPORT_SETUP,
            }

    if not output_path.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "error": "obsidian-export PDF render failed",
            "detail": detail[-3000:],
            "setup_hint": OBSIDIAN_EXPORT_SETUP,
        }

    result = _file_result(title, "pdf", output_path, renderer="obsidian-export")
    if cleanup_warning:
        result["warning"] = cleanup_warning
    return result


def _run_obsidian_export(cmd: list[str], timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(EXPORT_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def _flowing_pdf_input_path(work_dir: Path, source_path: Path, filename: Any, title: str) -> Path:
    markdown = source_path.read_text(encoding="utf-8", errors="replace")
    flowing = _flowing_pdf_markdown(markdown, title)
    if flowing == markdown:
        return source_path
    input_path = _unique_input_path(work_dir, filename or source_path.stem, title)
    input_path.write_text(flowing, encoding="utf-8")
    return input_path


def _flowing_pdf_markdown(markdown: str, title: str = "") -> str:
    """Prepare Markdown for flowing PDF output without filename titles or divider page breaks."""
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    frontmatter, body = _split_frontmatter(text)
    body_title, body = _extract_leading_h1(body)
    pdf_title = str(frontmatter.get("title") or body_title or title or "Report").strip()
    if pdf_title and not frontmatter.get("title"):
        frontmatter["title"] = pdf_title

    lines = body.splitlines()
    output: list[str] = []

    for line in lines:
        stripped = line.strip()
        is_rule = stripped in {"---", "***", "___"}
        if is_rule:
            if output and output[-1].strip():
                output.append("")
            continue
        output.append(line)

    body = "\n".join(output).strip()
    return _frontmatter_block(frontmatter) + (body + "\n" if body else "")


def _split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, markdown
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            raw = "\n".join(lines[1:idx])
            loaded = yaml.safe_load(raw) if raw.strip() else {}
            metadata = loaded if isinstance(loaded, dict) else {}
            return metadata, "\n".join(lines[idx + 1 :])
    return {}, markdown


def _extract_leading_h1(markdown: str) -> tuple[str, str]:
    lines = markdown.lstrip().splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        return title, "\n".join(lines[1:]).lstrip()
    return "", markdown


def _frontmatter_block(metadata: dict[str, Any]) -> str:
    if not metadata:
        return ""
    return "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n"


def _looks_like_missing_profile(completed: subprocess.CompletedProcess[str]) -> bool:
    detail = f"{completed.stderr or ''}\n{completed.stdout or ''}".lower()
    return "profile" in detail and any(token in detail for token in ("not found", "missing", "does not exist", "no such"))


def _unique_input_path(work_dir: Path, filename: Any, title: str) -> Path:
    stem_source = Path(str(filename or "")).stem if filename else title
    stem = _safe_slug(stem_source, "Obsidian-PDF-Input")
    path = work_dir / f"{stem}.md"
    counter = 2
    while path.exists():
        path = work_dir / f"{stem}-{counter}.md"
        counter += 1
    return path


def _export_process_env() -> dict[str, str]:
    env = dict(os.environ)
    path_parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    candidate_dirs = [
        Path.home() / "AppData" / "Local" / "Pandoc",
        Path.home() / "bin",
    ]
    for candidate in candidate_dirs:
        if candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in path_parts:
                path_parts.insert(0, candidate_str)
    env["PATH"] = os.pathsep.join(path_parts)
    return env


def _find_obsidian_export_exe() -> str | None:
    found = shutil.which("obsidian-export")
    if found:
        return found
    backend_dir = Path(__file__).resolve().parent
    candidates = [
        backend_dir / ".venv" / "Scripts" / "obsidian-export.exe",
        backend_dir / ".venv" / "Scripts" / "obsidian-export.cmd",
        backend_dir / ".venv" / "bin" / "obsidian-export",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


async def presenton_generate_pptx(params: dict[str, Any]) -> dict[str, Any]:
    """Generate an editable PPTX using a self-hosted Presenton API."""

    enabled = str(os.environ.get("PRESENTON_ENABLED") or "").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return {
            "ok": False,
            "error": "Presenton is disabled by PRESENTON_ENABLED=false. Use export_artifact for local PPTX fallback.",
        }

    title = str(params.get("title") or "Presenton Presentation").strip() or "Presenton Presentation"
    content = _presenton_content(params, title)
    if not content.strip():
        return {"ok": False, "error": "content, content_markdown, or slides is required"}

    base_url = str(
        params.get("base_url")
        or os.environ.get("PRESENTON_BASE_URL")
        or "http://localhost:5000"
    ).rstrip("/")
    api_key = str(params.get("api_key") or os.environ.get("PRESENTON_API_KEY") or "").strip()
    username = str(
        params.get("username")
        or os.environ.get("PRESENTON_USERNAME")
        or os.environ.get("PRESENTON_AUTH_USERNAME")
        or ""
    ).strip()
    password = str(
        params.get("password")
        or os.environ.get("PRESENTON_PASSWORD")
        or os.environ.get("PRESENTON_AUTH_PASSWORD")
        or ""
    )
    export_as = str(params.get("export_as") or "pptx").strip().lower()
    if export_as not in {"pptx", "pdf"}:
        return {"ok": False, "error": "export_as must be 'pptx' or 'pdf'"}

    n_slides = int(params.get("n_slides") or params.get("slide_count") or 8)
    n_slides = min(max(n_slides, 1), 60)
    payload = {
        "content": content,
        "n_slides": n_slides,
        "language": str(params.get("language") or "English"),
        "template": str(params.get("template") or "general"),
        "export_as": export_as,
        "tone": str(params.get("tone") or "professional"),
        "verbosity": str(params.get("verbosity") or "concise"),
        "include_title_slide": bool(params.get("include_title_slide", True)),
        "include_table_of_contents": bool(params.get("include_table_of_contents", False)),
        "web_search": bool(params.get("web_search", False)),
    }
    instructions = _presenton_layout_instructions(str(params.get("instructions") or "").strip())
    if instructions:
        payload["instructions"] = instructions

    slides_markdown = _slides_markdown(params.get("slides"))
    if slides_markdown:
        payload["slides_markdown"] = slides_markdown

    headers = {"Content-Type": "application/json"}
    auth = None
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif username and password:
        auth = httpx.BasicAuth(username, password)

    generate_url = f"{base_url}/api/v1/ppt/presentation/generate"
    try:
        async with httpx.AsyncClient(timeout=float(params.get("timeout_seconds") or 300), follow_redirects=True) as client:
            response = await client.post(generate_url, headers=headers, json=payload, auth=auth)
            response.raise_for_status()
            data = response.json()
            download_ref = data.get("path")
            if not download_ref:
                return {"ok": False, "error": "Presenton response did not include a path", "response": data}

            download_url = _presenton_download_url(base_url, str(download_ref))
            file_response = await client.get(download_url, headers=headers, auth=auth)
            file_response.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return {
            "ok": False,
            "error": f"Could not connect to Presenton at {base_url}. Start the self-hosted Presenton server and set PRESENTON_BASE_URL if it is not on port 5000.",
        }
    except (httpx.ReadTimeout, httpx.PoolTimeout):
        return {
            "ok": False,
            "error": f"Presenton at {base_url} did not respond before the timeout. Increase timeout_seconds or check that the self-hosted server is healthy.",
        }
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:2000] if exc.response is not None else str(exc)
        result = {
            "ok": False,
            "error": f"Presenton API returned HTTP {exc.response.status_code}",
            "detail": detail,
        }
        if exc.response.status_code in {401, 403}:
            result["auth_hint"] = (
                "Self-hosted Presenton requires HTTP Basic auth for /api/v1 routes. "
                "Set PRESENTON_USERNAME and PRESENTON_PASSWORD to the local admin login, "
                "or pass api_key for Presenton Cloud."
            )
        return result
    except httpx.RequestError as exc:
        return {"ok": False, "error": f"Presenton request failed for {base_url}: {type(exc).__name__}: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"Presenton generation failed: {type(exc).__name__}: {exc}"}

    output_path = _unique_export_path(params.get("filename"), title, export_as)
    output_path.write_bytes(file_response.content)

    result = _file_result(title, export_as, output_path, renderer="presenton")
    result.update({
        "presentation_id": data.get("presentation_id"),
        "presenton_path": data.get("path"),
        "presenton_edit_path": _presenton_download_url(base_url, str(data.get("edit_path"))) if data.get("edit_path") else data.get("edit_path"),
        "credits_consumed": data.get("credits_consumed"),
        "base_url": base_url,
    })
    return result


def _presenton_content(params: dict[str, Any], title: str) -> str:
    content = str(params.get("content") or params.get("content_markdown") or "").strip()
    if content:
        return content
    slides = params.get("slides")
    if not isinstance(slides, list):
        return ""
    parts = [f"# {title}", ""]
    for idx, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue
        parts.append(f"## Slide {idx}: {_fit_text(slide.get('title') or 'Untitled', 58)}")
        for key, limit in (("purpose", 90), ("subtitle", 90), ("takeaway", 95), ("visual", 95)):
            value = str(slide.get(key) or "").strip()
            if value:
                parts.append(f"{key.title()}: {_fit_text(value, limit)}")
        bullets = slide.get("bullets")
        if isinstance(bullets, list):
            parts.extend(f"- {_fit_text(bullet, 105)}" for bullet in bullets[:4] if str(bullet).strip())
        notes = str(slide.get("notes") or "").strip()
        if notes:
            parts.extend(["", f"Notes: {notes}"])
        parts.append("")
    return "\n".join(parts)


def _slides_markdown(slides: Any) -> list[str] | None:
    if not isinstance(slides, list):
        return None
    output: list[str] = []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        lines = [f"# {_fit_text(slide.get('title') or 'Untitled', 58)}"]
        subtitle = _fit_text(str(slide.get("subtitle") or slide.get("purpose") or "").strip(), 90)
        if subtitle:
            lines.extend(["", subtitle])
        bullets = slide.get("bullets")
        if isinstance(bullets, list) and bullets:
            lines.append("")
            lines.extend(f"- {_fit_text(bullet, 105)}" for bullet in bullets[:4] if str(bullet).strip())
        takeaway = _fit_text(str(slide.get("takeaway") or "").strip(), 95)
        if takeaway:
            lines.extend(["", f"**Takeaway:** {takeaway}"])
        output.append("\n".join(lines))
    return output or None


def _presenton_layout_instructions(user_instructions: str) -> str:
    layout_contract = (
        "Strict PPTX layout constraints: every slide must avoid text overflow and overlap. "
        "Keep titles to one line when possible, max 58 characters. Keep subtitles max 90 "
        "characters and place them below the title with clear vertical separation. Use no "
        "more than 4 bullets per slide, max 105 characters per bullet. Keep takeaway, "
        "summary, or conclusion text max 95 characters in a separate bottom area with "
        "clear spacing from the bullet body. Use smaller fonts or shorter text rather than "
        "letting text exceed its own text area. Do not place body bullets over the bottom "
        "summary/conclusion. The deck should be visually presentable and easy to scan."
    )
    return f"{layout_contract}\n\n{user_instructions}".strip()


def _fit_text(value: Any, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "..."


def _presenton_download_url(base_url: str, path_or_url: str) -> str:
    if not path_or_url:
        return path_or_url
    parsed = urlparse(path_or_url)
    if parsed.scheme in {"http", "https"}:
        return path_or_url
    return urljoin(f"{base_url}/", path_or_url.lstrip("/"))


def _file_result(title: str, fmt: str, path: Path, renderer: str) -> dict[str, Any]:
    return {
        "ok": True,
        "title": title,
        "format": fmt,
        "renderer": renderer,
        "filename": path.name,
        "path": str(path),
        "download_url": f"/api/exports/{path.name}",
        "backend_url": f"/exports/{path.name}",
        "size_bytes": path.stat().st_size,
    }
