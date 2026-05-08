"""Workflow-aware MCP tools for local wiki-style knowledge bases.

These tools intentionally stop at raw capture, ingest planning, and explicit
status/log updates. The synthesis step remains human/agent-approved because the
target KBs are curated markdown wikis with their own local rules.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


ULTRASOUND_ROOT = Path(r"C:\Users\jianh\Projects\jhm-ultrasound-kb")
LLM_WIKI_HUB = Path(r"C:\Users\jianh\wiki")


KB_CONFIGS: dict[str, dict[str, Any]] = {
    "ultrasound": {
        "name": "ultrasound_kb_actions",
        "root": ULTRASOUND_ROOT,
        "raw_root": ULTRASOUND_ROOT / "raw",
        "raw_export_root": ULTRASOUND_ROOT / "raw" / "00-index" / "from-obsidian-ai",
        "wiki_root": ULTRASOUND_ROOT / "wiki",
        "synthesis_export_root": ULTRASOUND_ROOT / "wiki" / "syntheses" / "_inbox" / "from-obsidian-ai",
        "index_path": ULTRASOUND_ROOT / "wiki" / "index.md",
        "log_path": ULTRASOUND_ROOT / "wiki" / "log.md",
        "rules_paths": [
            ULTRASOUND_ROOT / "AGENTS.md",
            ULTRASOUND_ROOT / ".agents" / "skills" / "ingest" / "SKILL.md",
        ],
        "raw_targets": ["00-index", "01-articles", "02-papers", "03-transcripts", "04-meeting_notes"],
        "requires_topic": False,
    },
    "llm_wiki": {
        "name": "llm_wiki_actions",
        "root": LLM_WIKI_HUB,
        "requires_topic": True,
        "raw_targets": ["articles", "papers", "notes", "data", "repos"],
    },
}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "untitled"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_text(path: Path, limit: int = 12000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]


def _load_hub_wikis() -> dict[str, dict[str, Any]]:
    config_path = LLM_WIKI_HUB / "wikis.json"
    if not config_path.exists():
        return {}
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return data.get("wikis", {})


def _hub_topic_names() -> list[str]:
    return sorted(name for name in _load_hub_wikis() if name != "hub")


def _safe_child(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError(f"Path is outside configured KB root: {candidate}")
    return candidate_resolved


def _availability(config: dict[str, Any]) -> dict[str, Any]:
    required = {
        "root": config["root"],
        "raw_root": config["raw_root"],
        "wiki_root": config["wiki_root"],
        "index_path": config["index_path"],
        "log_path": config["log_path"],
    }
    missing = [name for name, path in required.items() if not path.exists()]
    return {
        "available": not missing,
        "missing": missing,
        "paths": {name: str(path) for name, path in required.items()},
    }


def _require_available(config: dict[str, Any]) -> None:
    status = _availability(config)
    if not status["available"]:
        missing = ", ".join(status["missing"])
        raise ValueError(f"Configured KB is unavailable or incomplete; missing: {missing}")


def _topic_config(topic: str | None) -> dict[str, Any]:
    if not topic:
        raise ValueError("This KB action server requires a topic name.")
    if topic == "hub":
        raise ValueError("Use a concrete topic wiki, not the hub root.")
    wikis = _load_hub_wikis()
    item = wikis.get(topic)
    if not item:
        raise ValueError(f"Unknown LLM Wiki topic: {topic}")
    root = Path(item["path"])
    return {
        "name": topic,
        "root": root,
        "raw_root": root / "raw",
        "wiki_root": root / "wiki",
        "index_path": root / "wiki" / "_index.md",
        "log_path": root / "log.md",
        "rules_paths": [
            root / "config.md",
            LLM_WIKI_HUB / "_index.md",
        ],
        "raw_targets": KB_CONFIGS["llm_wiki"]["raw_targets"],
        "requires_topic": True,
    }


def _resolve_config(base: dict[str, Any], topic: str | None = None) -> dict[str, Any]:
    if base.get("requires_topic"):
        return _topic_config(topic)
    return base


def _job_dir(config: dict[str, Any]) -> Path:
    path = config["root"] / ".action-mcp" / "ingest-requests"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _command_request_dir(config: dict[str, Any]) -> Path:
    path = config["root"] / ".action-mcp" / "command-requests"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _relative_to_root(config: dict[str, Any], path: Path) -> str:
    try:
        return path.resolve().relative_to(config["root"].resolve()).as_posix()
    except ValueError:
        return str(path)


def _record_followup_command(
    config: dict[str, Any],
    command_name: str,
    target_path: Path,
    summary: str,
) -> dict[str, Any]:
    rel_path = _relative_to_root(config, target_path)
    command = f"/{command_name} {rel_path}"
    job = {
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "command_name": command_name,
        "target_path": str(target_path),
        "target_path_relative": rel_path,
        "summary": summary,
    }
    job_path = _command_request_dir(config) / f"{_now_stamp()}-{command_name}-{_slugify(target_path.stem)}.json"
    job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "invoked": True,
        "command": command,
        "command_name": command_name,
        "request_path": str(job_path),
        "status": "pending",
    }


def _safe_path_parts(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in Path(value).parts if part not in {"", ".", ".."}]


def _append_subfolder_once(base_dir: Path, subfolder: str | None) -> Path:
    target_dir = base_dir
    for part in _safe_path_parts(subfolder):
        if target_dir.name.lower() == part.lower():
            continue
        target_dir = target_dir / part
    return target_dir


def _markdown_filename(filename: str | None, default_name: str) -> str:
    name = Path(filename).name if filename else default_name
    if not name.lower().endswith(".md"):
        name += ".md"
    return name


def build_server(config_name: str) -> FastMCP:
    base_config = KB_CONFIGS[config_name]
    mcp = FastMCP(base_config["name"])

    @mcp.tool()
    def kb_status(topic: str | None = None) -> dict[str, Any]:
        """Return configured paths, available raw targets, topics, and index/log presence."""
        if base_config.get("requires_topic") and not topic:
            return {
                "kb": base_config["name"],
                "root": str(base_config["root"]),
                "requires_topic": True,
                "topics": _hub_topic_names(),
                "next_step": "Call kb_status again with a topic name before saving or requesting ingest.",
        }
        config = _resolve_config(base_config, topic)
        availability = _availability(config)
        status: dict[str, Any] = {
            "kb": base_config["name"],
            "root": str(config["root"]),
            "raw_root": str(config["raw_root"]),
            "wiki_root": str(config["wiki_root"]),
            "index_path": str(config["index_path"]),
            "index_exists": config["index_path"].exists(),
            "log_path": str(config["log_path"]),
            "log_exists": config["log_path"].exists(),
            "raw_targets": config["raw_targets"],
            "requires_topic": config.get("requires_topic", False),
            "available": availability["available"],
            "missing": availability["missing"],
        }
        if base_config.get("requires_topic"):
            status["topics"] = _hub_topic_names()
        return status

    @mcp.tool()
    def get_ingest_rules(topic: str | None = None) -> dict[str, str]:
        """Read the KB-specific ingest rules that an agent must follow before changing wiki files."""
        config = _resolve_config(base_config, topic)
        _require_available(config)
        rules = {}
        for path in config["rules_paths"]:
            rules[str(path)] = _read_text(path)
        return rules

    @mcp.tool()
    def save_to_raw(
        title: str,
        content: str,
        raw_target: str | None = None,
        filename: str | None = None,
        source: str | None = None,
        tags: list[str] | None = None,
        subfolder: str | None = None,
        invoke_followup: bool = True,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Save content as a raw source file for later reviewed ingest."""
        config = _resolve_config(base_config, topic)
        _require_available(config)
        target = raw_target or ("01-articles" if config_name == "ultrasound" else "notes")
        if target not in config["raw_targets"]:
            raise ValueError(f"Unsupported raw_target '{target}'. Use one of: {config['raw_targets']}")

        if config_name == "ultrasound" and (raw_target is None or target == "00-index") and subfolder is None:
            raw_dir = _safe_child(config["raw_root"], config.get("raw_export_root", config["raw_root"] / target))
            target = _relative_to_root(config, raw_dir)
        else:
            raw_dir = _safe_child(config["raw_root"], config["raw_root"] / target)
            if subfolder:
                raw_dir = _safe_child(raw_dir, _append_subfolder_once(raw_dir, subfolder))
        raw_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _markdown_filename(filename, f"{_now_stamp()}-{_slugify(title)}.md")
        path = _safe_child(raw_dir, raw_dir / safe_name)
        if path.exists():
            path = _safe_child(raw_dir, raw_dir / f"{_now_stamp()}-{path.name}")

        frontmatter = {
            "title": title,
            "saved_by": base_config["name"],
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "source": source or "",
            "tags": tags or [],
            "ingest_status": "pending_review",
        }
        header = "---\n" + "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()
        ) + "\n---\n\n"
        path.write_text(header + content.strip() + "\n", encoding="utf-8")
        followup = (
            _record_followup_command(config, "ingest", path, f"Ingest raw import from Obsidian AI: {title}")
            if invoke_followup
            else None
        )
        return {
            "saved": True,
            "path": str(path),
            "raw_target": target,
            "selected_action": "save-to-raw",
            "follow_up": followup,
            "next_step": f"Follow-up command recorded: {followup['command']}" if followup else "Run /ingest for the saved raw file.",
        }

    @mcp.tool()
    def save_to_synthesis(
        title: str,
        content: str,
        filename: str | None = None,
        source: str | None = None,
        tags: list[str] | None = None,
        subfolder: str | None = None,
        invoke_followup: bool = True,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Export reviewed Markdown directly into the KB synthesis area."""
        config = _resolve_config(base_config, topic)
        _require_available(config)
        export_root = config.get("synthesis_export_root")
        if export_root is None:
            raise ValueError("This KB does not define a synthesis export folder.")

        target_dir = _safe_child(config["wiki_root"], Path(export_root))
        if subfolder:
            target_dir = _safe_child(target_dir, _append_subfolder_once(target_dir, subfolder))
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _markdown_filename(filename, f"{_slugify(title)}.md")
        path = _safe_child(target_dir, target_dir / safe_name)
        if path.exists():
            path = _safe_child(target_dir, target_dir / f"{_now_stamp()}-{path.name}")

        frontmatter = {
            "title": title,
            "exported_by": base_config["name"],
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": source or "",
            "tags": tags or [],
            "origin": "obsidian-ai-internal-kb",
        }
        header = "---\n" + "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()
        ) + "\n---\n\n"
        path.write_text(header + content.strip() + "\n", encoding="utf-8")
        followup = (
            _record_followup_command(config, "sync-import", path, f"Sync synthesis import from Obsidian AI: {title}")
            if invoke_followup
            else None
        )
        return {
            "saved": True,
            "path": str(path),
            "folder": str(target_dir),
            "selected_action": "save-to-synthesis",
            "follow_up": followup,
            "next_step": f"Follow-up command recorded: {followup['command']}" if followup else "Run /sync-import for the saved synthesis file.",
        }

    @mcp.tool()
    def preview_ingest_plan(raw_path: str, topic: str | None = None) -> dict[str, Any]:
        """Validate a raw file and return a reviewed-ingest checklist without modifying wiki files."""
        config = _resolve_config(base_config, topic)
        _require_available(config)
        path = _safe_child(config["raw_root"], Path(raw_path))
        if not path.exists() or not path.is_file():
            raise ValueError(f"Raw file not found: {raw_path}")

        snippet = _read_text(path, limit=4000)
        return {
            "raw_path": str(path),
            "requires_human_approval": True,
            "rules_to_read": [str(p) for p in config["rules_paths"]],
            "index_to_update": str(config["index_path"]),
            "log_to_append": str(config["log_path"]),
            "suggested_steps": [
                "Read the raw source and KB ingest rules.",
                "Identify whether the output should be a source, concept, entity, or synthesis page.",
                "Inspect existing index and related wiki pages before writing.",
                "Create or update wiki pages with frontmatter and bidirectional links.",
                "Update the wiki index and append the ingest log.",
                "Run lint or a manual link/index check.",
            ],
            "content_preview": snippet,
        }

    @mcp.tool()
    def create_ingest_request(
        raw_path: str,
        request_summary: str,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Record a pending ingest request for human-approved processing."""
        config = _resolve_config(base_config, topic)
        _require_available(config)
        path = _safe_child(config["raw_root"], Path(raw_path))
        if not path.exists() or not path.is_file():
            raise ValueError(f"Raw file not found: {raw_path}")

        job = {
            "status": "pending_approval",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kb": base_config["name"],
            "topic": topic,
            "raw_path": str(path),
            "request_summary": request_summary,
            "rules_to_follow": [str(p) for p in config["rules_paths"]],
            "index_to_update": str(config["index_path"]),
            "log_to_append": str(config["log_path"]),
        }
        job_path = _job_dir(config) / f"{_now_stamp()}-{_slugify(path.stem)}.json"
        job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "created": True,
            "job_path": str(job_path),
            "status": "pending_approval",
            "next_step": "A human or Codex session should approve and run the KB ingest workflow.",
        }

    @mcp.tool()
    def list_ingest_requests(topic: str | None = None) -> list[dict[str, Any]]:
        """List pending or recorded ingest requests for this KB."""
        config = _resolve_config(base_config, topic)
        _require_available(config)
        jobs = []
        directory = _job_dir(config)
        for path in sorted(directory.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                item = {"status": "unreadable"}
            item["job_path"] = str(path)
            jobs.append(item)
        return jobs

    @mcp.tool()
    def append_workflow_log(
        action: str,
        summary: str,
        raw_path: str | None = None,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Append an explicit workflow status line to the KB log."""
        config = _resolve_config(base_config, topic)
        _require_available(config)
        if action not in {"save", "ingest-request", "ingest", "lint", "sync", "note"}:
            raise ValueError("action must be one of: save, ingest-request, ingest, lint, sync, note")
        log_path = config["log_path"]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        raw_line = f"\n- raw: {raw_path}" if raw_path else ""
        entry = f"\n## [{_now_date()}] {action} | {summary}{raw_line}\n"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(entry)
        return {"appended": True, "log_path": str(log_path), "entry": entry.strip()}

    @mcp.tool()
    def codex_ingest_prompt(raw_path: str, topic: str | None = None) -> dict[str, str]:
        """Generate a prompt for a Codex session to run the approved ingest workflow."""
        config = _resolve_config(base_config, topic)
        _require_available(config)
        path = _safe_child(config["raw_root"], Path(raw_path))
        prompt = (
            f"Run the local KB ingest workflow for {path}. "
            f"Work in {config['root']}. Read the KB rules at "
            f"{', '.join(str(p) for p in config['rules_paths'])}. "
            f"Update {config['index_path']} and append {config['log_path']}. "
            "Preserve raw files unless the local rules explicitly say to archive them, "
            "and report every wiki file changed."
        )
        return {"prompt": prompt}

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb", choices=sorted(KB_CONFIGS), required=True)
    args = parser.parse_args()
    build_server(args.kb).run()


if __name__ == "__main__":
    main()
