import asyncio
import json
import os
import uuid
from collections import OrderedDict
from pathlib import Path

import uvicorn
from agents import set_tracing_disabled
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

# VERTEXAI_PROJECT and TAVILY_API_KEY come from the .env file next to this one.
load_dotenv(Path(__file__).parent / ".env")

from context_panel import AgentContexts, add_context_panel  # noqa: E402
from research import ResearchHooks, deep_research  # noqa: E402

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)

HERE = Path(__file__).parent

# run_id -> every agent's context, in order. In-memory, single process. The context panel reads it.
sessions: dict[str, AgentContexts] = {}
# session_id -> the question waiting on the user's answer to a clarifying question
pending: dict[str, dict] = {}
# session_id -> the research state its last report left, so a follow-up question reuses its findings
# instead of starting over. In-memory: a restart (or another Cloud Run instance) forgets it, and the
# follow-up is then researched from scratch. Oldest conversations are dropped past MAX_CONVERSATIONS.
conversations: OrderedDict[str, object] = OrderedDict()
MAX_CONVERSATIONS = 100

app = FastAPI()
add_context_panel(app, sessions)


def compose(session_id: str, message: str) -> str:
    """Join a reply to a clarifying question with the question it clarifies."""
    waiting = pending.pop(session_id, None)
    if waiting is None:
        return message
    return f"{waiting['question']}\n\nClarifying question: {waiting['asked']}\nUser's answer: {message}"


def remember(session_id: str, question: str, result: dict) -> None:
    if result["type"] == "clarify":
        pending[session_id] = {"question": question, "asked": result["markdown"]}
    elif result["type"] == "report" and result.get("state") is not None:
        conversations[session_id] = result["state"]
        conversations.move_to_end(session_id)
        while len(conversations) > MAX_CONVERSATIONS:
            conversations.popitem(last=False)


@app.get("/")
def index():
    # no-cache: browsers otherwise keep serving an old page after a redeploy
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/playbook.js")
def playbook():
    return FileResponse(HERE / "playbook.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/run")
async def run(question: str, session_id: str | None = None):
    """Research one question and stream what happens as Server-Sent Events. Used by the page."""
    session_id = session_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = ResearchHooks(sessions[run_id], queue.put_nowait)
    full_question = compose(session_id, question)

    async def work():
        try:
            result = await deep_research(full_question, hooks, conversations.get(session_id))
            remember(session_id, full_question, result)
            queue.put_nowait({
                "follow_up": len(result["state"].history) > 1 if result.get("state") else False,
                "type": result["type"],
                "markdown": result["markdown"],
                "tool_calls": len(result["tool_calls"]),
                "findings": len(result.get("findings", [])),
                "rejected": len(result.get("rejected", [])),
            })
        except Exception as e:
            # Auth, quota, a model that won't stop: show it on the page, not as a dropped stream.
            queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"})

    task = asyncio.create_task(work())

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': run_id, 'session_id': session_id})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("report", "clarify", "error"):
                    break
        finally:
            task.cancel()  # the page closed the stream: stop spending tokens

    return StreamingResponse(events(), media_type="text/event-stream")


# --- /chat: the Project 1 response shape ---


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]  # every call, the sub-agents' included, each with an `agent` field


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    hooks = ResearchHooks(AgentContexts(), lambda event: None)
    question = compose(session_id, request.message)
    try:
        result = await deep_research(question, hooks, conversations.get(session_id))
        remember(session_id, question, result)
        response, tool_calls = result["markdown"], result["tool_calls"]
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        response, tool_calls = f"Research failed: {type(e).__name__}: {str(e)[:300]}", hooks.tool_calls
    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
async def clear(session_id: str | None = None):
    if session_id:
        pending.pop(session_id, None)
        conversations.pop(session_id, None)
    return {"status": "ok"}


if __name__ == "__main__":
    # Cloud Run sets PORT and needs 0.0.0.0; locally, stay on localhost.
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0" if "PORT" in os.environ else "127.0.0.1", port=port)
