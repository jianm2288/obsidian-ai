import json
import logging
from datetime import datetime, timezone

from models import KnowledgeBase, KnowledgeBaseDocument

logger = logging.getLogger(__name__)


def _workflow_config(workflow) -> dict:
    raw = getattr(workflow, "config_json", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        logger.warning("Workflow %s has invalid config_json", getattr(workflow, "id", "?"))
        return {}


def auto_save_workflow_output_sqlite(workflow, run_id: int, final_output: str | None, db) -> dict | None:
    """Persist completed workflow output to a configured knowledge base.

    Enabled by setting workflow.config_json to include:
    {"auto_save_kb_id": "1"}
    """
    config = _workflow_config(workflow)
    kb_id = config.get("auto_save_kb_id")
    if not kb_id or not final_output or not final_output.strip():
        return None

    try:
        kb_id_int = int(kb_id)
    except (TypeError, ValueError):
        logger.warning("Workflow %s has invalid auto_save_kb_id=%r", workflow.id, kb_id)
        return None

    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == kb_id_int,
        KnowledgeBase.user_id == workflow.user_id,
        KnowledgeBase.is_active == True,
    ).first()
    if not kb:
        logger.warning("Workflow %s auto-save KB %s not found", workflow.id, kb_id)
        return None

    now = datetime.now(timezone.utc)
    doc_name = config.get("auto_save_doc_name") or (
        f"{workflow.name} run {run_id} - {now.strftime('%Y-%m-%d %H:%M UTC')}"
    )

    doc = KnowledgeBaseDocument(
        kb_id=kb.id,
        doc_type="text",
        name=doc_name,
        content_text=final_output,
        indexed=False,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    indexed = False
    try:
        from rag_service import RAGService

        RAGService.index_kb_document(
            str(kb.id),
            final_output,
            {
                "doc_name": doc.name,
                "filename": None,
                "workflow_id": str(workflow.id),
                "workflow_run_id": str(run_id),
            },
        )
        doc.indexed = True
        db.commit()
        indexed = True
    except Exception as exc:
        logger.warning("Workflow %s saved doc %s but indexing failed: %s", workflow.id, doc.id, exc)

    return {
        "kb_id": str(kb.id),
        "kb_name": kb.name,
        "doc_id": str(doc.id),
        "doc_name": doc.name,
        "indexed": indexed,
    }
