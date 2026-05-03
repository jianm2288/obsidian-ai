import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from encryption import encrypt_api_key
from models import Agent, KnowledgeBase, LLMProvider, MCPServer, ToolDefinition, User, Workflow

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
SEED_PATH = BACKEND_DIR / "seed" / "default_workspace.json"


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _backend_python() -> str:
    candidates = [
        BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
        BACKEND_DIR / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _which_or_name(command: str, fallbacks: list[Path] | None = None) -> str:
    found = shutil.which(command)
    if found:
        return found
    for candidate in fallbacks or []:
        if candidate.exists():
            return str(candidate)
    return command


def _tokens() -> dict[str, str]:
    return {
        "{PROJECT_ROOT}": str(PROJECT_ROOT),
        "{BACKEND_DIR}": str(BACKEND_DIR),
        "{USER_HOME}": str(Path.home()),
        "{BACKEND_PYTHON}": _backend_python(),
        "{NPX}": _which_or_name("npx"),
        "{UVX}": _which_or_name("uvx", [Path.home() / ".local" / "bin" / "uvx.exe"]),
    }


def _resolve(value: Any) -> Any:
    if isinstance(value, str):
        resolved = value
        for token, replacement in _tokens().items():
            resolved = resolved.replace(token, replacement)
        return resolved
    if isinstance(value, list):
        return [_resolve(item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item) for key, item in value.items()}
    return value


def _load_seed() -> dict[str, Any]:
    with SEED_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _target_users(db: Session, user_id: int | None) -> list[User]:
    query = db.query(User)
    if user_id is not None:
        user = query.filter(User.id == int(user_id)).first()
        return [user] if user else []
    return query.order_by(User.id).all()


def _query_by_name(db: Session, model: Any, user_id: int, name: str) -> Any:
    return db.query(model).filter(model.user_id == user_id, model.name == name).first()


def _create_provider(db: Session, user_id: int, data: dict[str, Any]) -> LLMProvider:
    api_key = None
    api_key_env = data.get("api_key_env")
    if api_key_env and os.environ.get(api_key_env):
        api_key = encrypt_api_key(os.environ[api_key_env])
    base_url_env = data.get("base_url_env")
    base_url = os.environ.get(base_url_env) if base_url_env else None

    return LLMProvider(
        user_id=user_id,
        name=data["name"],
        provider_type=data["provider_type"],
        base_url=base_url or _resolve(data.get("base_url")),
        api_key=api_key,
        model_id=data.get("model_id"),
        is_active=data.get("is_active", True),
        config_json=_json_dumps(data.get("config")),
    )


def _sync_user_workspace(db: Session, user: User, seed: dict[str, Any]) -> dict[str, int]:
    counts = {
        "providers": 0,
        "tools": 0,
        "mcp_servers": 0,
        "knowledge_bases": 0,
        "agents": 0,
        "workflows": 0,
    }

    provider_ids: dict[str, int] = {}
    tool_ids: dict[str, int] = {}
    mcp_ids: dict[str, int] = {}
    kb_ids: dict[str, int] = {}
    agent_ids: dict[str, int] = {}

    for item in seed.get("providers", []):
        provider = _query_by_name(db, LLMProvider, user.id, item["name"])
        if provider is None:
            provider = _create_provider(db, user.id, item)
            db.add(provider)
            db.flush()
            counts["providers"] += 1
        provider_ids[item["name"]] = provider.id

    for item in seed.get("tools", []):
        tool = _query_by_name(db, ToolDefinition, user.id, item["name"])
        if tool is None:
            tool = ToolDefinition(
                user_id=user.id,
                name=item["name"],
                description=item.get("description"),
                parameters_json=_json_dumps(item.get("parameters") or {}),
                handler_type=item.get("handler_type", "http"),
                handler_config=_json_dumps(_resolve(item.get("handler_config"))),
                requires_confirmation=item.get("requires_confirmation", False),
                is_active=item.get("is_active", True),
                is_model_created=item.get("is_model_created", False),
            )
            db.add(tool)
            db.flush()
            counts["tools"] += 1
        tool_ids[item["name"]] = tool.id

    for item in seed.get("mcp_servers", []):
        server = _query_by_name(db, MCPServer, user.id, item["name"])
        if server is None:
            server = MCPServer(
                user_id=user.id,
                name=item["name"],
                description=item.get("description"),
                transport_type=item["transport_type"],
                command=_resolve(item.get("command")),
                args_json=_json_dumps(_resolve(item.get("args"))),
                env_json=_json_dumps(_resolve(item.get("env"))),
                url=_resolve(item.get("url")),
                headers_json=_json_dumps(_resolve(item.get("headers"))),
                is_active=item.get("is_active", True),
            )
            db.add(server)
            db.flush()
            counts["mcp_servers"] += 1
        mcp_ids[item["name"]] = server.id

    for item in seed.get("knowledge_bases", []):
        kb = _query_by_name(db, KnowledgeBase, user.id, item["name"])
        if kb is None:
            kb = KnowledgeBase(
                user_id=user.id,
                name=item["name"],
                description=item.get("description"),
                is_shared=item.get("is_shared", False),
                is_active=item.get("is_active", True),
            )
            db.add(kb)
            db.flush()
            counts["knowledge_bases"] += 1
        kb_ids[item["name"]] = kb.id

    for item in seed.get("agents", []):
        agent = _query_by_name(db, Agent, user.id, item["name"])
        if agent is None:
            provider_name = item.get("provider_name")
            provider_id = provider_ids.get(provider_name) if provider_name else None
            resolved_tool_ids = [str(tool_ids[name]) for name in item.get("tools", []) if name in tool_ids]
            resolved_mcp_ids = [str(mcp_ids[name]) for name in item.get("mcp_servers", []) if name in mcp_ids]
            resolved_kb_ids = [str(kb_ids[name]) for name in item.get("knowledge_bases", []) if name in kb_ids]
            agent = Agent(
                user_id=user.id,
                name=item["name"],
                description=item.get("description"),
                system_prompt=item.get("system_prompt"),
                provider_id=provider_id,
                tools_json=_json_dumps(resolved_tool_ids) if resolved_tool_ids else None,
                mcp_servers_json=_json_dumps(resolved_mcp_ids) if resolved_mcp_ids else None,
                knowledge_base_ids_json=_json_dumps(resolved_kb_ids) if resolved_kb_ids else None,
                model_id=item.get("model_id"),
                hitl_confirmation_tools_json=_json_dumps(item.get("hitl_confirmation_tools") or []),
                allow_tool_creation=item.get("allow_tool_creation", False),
                memory_enabled=item.get("memory_enabled", True),
                sandbox_enabled=item.get("sandbox_enabled", False),
                config_json=_json_dumps(item.get("config")),
                is_active=item.get("is_active", True),
            )
            db.add(agent)
            db.flush()
            counts["agents"] += 1
        agent_ids[item["name"]] = agent.id

    for item in seed.get("workflows", []):
        workflow = _query_by_name(db, Workflow, user.id, item["name"])
        if workflow is not None:
            continue

        steps = []
        for step in item.get("steps", []):
            agent_name = step.get("agent_name")
            agent_id = agent_ids.get(agent_name)
            if not agent_id:
                logger.warning("Skipping workflow step for missing agent %r", agent_name)
                continue
            resolved_step = dict(step)
            resolved_step.pop("agent_name", None)
            resolved_step["agent_id"] = str(agent_id)
            steps.append(resolved_step)

        config = dict(item.get("config") or {})
        kb_name = config.pop("auto_save_kb_name", None)
        if kb_name and kb_name in kb_ids:
            config["auto_save_kb_id"] = str(kb_ids[kb_name])

        workflow = Workflow(
            user_id=user.id,
            name=item["name"],
            description=item.get("description"),
            steps_json=_json_dumps(steps),
            config_json=_json_dumps(config) if config else None,
            is_active=item.get("is_active", True),
        )
        db.add(workflow)
        db.flush()
        counts["workflows"] += 1

    return counts


def sync_default_workspace_sqlite(db: Session | None = None, user_id: int | None = None) -> dict[str, int]:
    """Create missing default agents, tools, MCP servers, KBs, and workflows.

    The sync is intentionally additive. Existing rows with the same user/name are
    left alone so local prompt and workflow edits survive normal restarts.
    """
    if not SEED_PATH.exists():
        logger.warning("Default workspace seed file not found: %s", SEED_PATH)
        return {}

    owns_session = db is None
    if db is None:
        from database import SessionLocal

        db = SessionLocal()

    totals: dict[str, int] = {}
    try:
        seed = _load_seed()
        users = _target_users(db, user_id)
        if not users:
            return {}

        for user in users:
            counts = _sync_user_workspace(db, user, seed)
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + value
        db.commit()

        created_total = sum(totals.values())
        if created_total:
            logger.info("Default workspace sync created %s rows: %s", created_total, totals)
        return totals
    except Exception:
        db.rollback()
        logger.exception("Default workspace sync failed")
        raise
    finally:
        if owns_session:
            db.close()


if __name__ == "__main__":
    sync_default_workspace_sqlite()
