"""REST API FastAPI & Fallback Server for E.R.I.I. Engine.

Enables Node.js, Go, Rust, Java, and C# client applications to call E.R.I.I. via HTTP REST API.
Follows Google Python Style Guide.
"""

import argparse
import json
import logging
import sys
from typing import Optional

from erii.engine import ERIIEngine
from erii.models.config import ERIIConfig

logger = logging.getLogger("erii.server")

# Initialize global engine instance for server mode
engine = ERIIEngine(storage_dir="./erii_memory")

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    app = FastAPI(
        title="E.R.I.I. Memory Engine REST API",
        description="Experiential Recall & Impression Integration Engine",
        version="0.1.0",
    )

    class RememberRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str
        user_message: str
        bot_reply: str

    class RecallRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str
        query: str
        top_k: int = 5

    class CoreMemoryRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str
        content: str

    class ThoughtRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str
        content: str
        visibility: str = "public_log"
        is_unresolved: bool = False
        emotional_score: float = 0.0
        foreshadowing_tags: Optional[list] = None
        created_at: Optional[str] = None

    class ResolveThoughtRequest(BaseModel):
        agent_id: str = "default_agent"
        user_id: str

    @app.post("/api/v1/remember")
    def api_remember(req: RememberRequest):
        """Records a conversation turn into memory."""
        try:
            engine.remember(
                agent_id=req.agent_id,
                user_id=req.user_id,
                user_message=req.user_message,
                bot_reply=req.bot_reply,
            )
            return {"status": "success", "message": "Turn logged for archival."}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/recall")
    def api_recall(req: RecallRequest):
        """Recalls formatted memory context for prompt injection."""
        try:
            context = engine.recall(
                agent_id=req.agent_id,
                user_id=req.user_id,
                query=req.query,
                top_k=req.top_k,
            )
            return {"status": "success", "context": context}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/core_memory")
    def api_set_core_memory(req: CoreMemoryRequest):
        """Sets Core Persona Memory string."""
        try:
            engine.set_core_memory(
                agent_id=req.agent_id, user_id=req.user_id, content=req.content
            )
            return {"status": "success", "message": "Core memory saved."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/v1/core_memory")
    def api_get_core_memory(agent_id: str, user_id: str):
        """Gets Core Persona Memory string."""
        try:
            content = engine.get_core_memory(agent_id=agent_id, user_id=user_id)
            return {"status": "success", "content": content}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/v1/memory/monologue")
    def api_get_monologue(
        user_id: str,
        agent_id: str = "default_agent",
        limit: int = 10,
        unresolved_only: bool = False,
        visibility: Optional[str] = "public_log",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ):
        """Retrieves inner monologue / diary entries."""
        try:
            monologues = engine.get_inner_monologue(
                agent_id=agent_id,
                user_id=user_id,
                limit=limit,
                unresolved_only=unresolved_only,
                visibility=visibility,
                start_time=start_time,
                end_time=end_time,
            )
            return {"status": "success", "monologues": monologues}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/memory/thought")
    def api_remember_thought(req: ThoughtRequest):
        """Explicitly records an inner monologue / diary entry."""
        try:
            node = engine.remember_thought(
                agent_id=req.agent_id,
                user_id=req.user_id,
                content=req.content,
                visibility=req.visibility,
                is_unresolved=req.is_unresolved,
                emotional_score=req.emotional_score,
                foreshadowing_tags=req.foreshadowing_tags,
                created_at=req.created_at,
            )
            return {"status": "success", "node": node.to_dict()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.patch("/api/v1/memory/thought/{node_id}/resolve")
    def api_resolve_thought(node_id: str, req: ResolveThoughtRequest):
        """Marks a suspenseful/unresolved thought as resolved."""
        try:
            success = engine.resolve_thought(
                agent_id=req.agent_id,
                user_id=req.user_id,
                node_id=node_id,
            )
            if not success:
                raise HTTPException(status_code=404, detail="Thought node not found.")
            return {"status": "success", "message": "Thought resolved successfully."}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

except ImportError:
    app = None  # FastAPI not installed


def cli_main():
    """CLI entrypoint for running `erii serve`."""
    parser = argparse.ArgumentParser(description="E.R.I.I. Engine Server CLI")
    parser.add_argument("command", choices=["serve"], help="Command to run")
    parser.add_argument("--host", default="0.0.0.0", help="Host address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--storage-dir", default="./erii_memory", help="Memory storage directory")
    args = parser.parse_args()

    if args.command == "serve":
        if app is None:
            print("Error: FastAPI and Uvicorn are required for running REST API server.")
            print("Please install them via: pip install 'erii[server]' or pip install fastapi uvicorn")
            sys.exit(1)

        import uvicorn
        print(f"Starting E.R.I.I. REST API Server at http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    cli_main()
