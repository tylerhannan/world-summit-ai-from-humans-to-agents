"""A small SQL agent: Claude plans and writes SQL, the ClickHouse MCP server runs it,
and every step is traced to Langfuse (tied to a user, a session and a prompt version)."""

import os
import re
import time

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from langfuse import get_client, propagate_attributes
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")
MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp")
MCP_TOKEN = os.getenv("CLICKHOUSE_MCP_AUTH_TOKEN")
PROMPT_NAME = os.getenv("PROMPT_NAME", "uk-property-agent")
MAX_STEPS = int(os.getenv("MAX_STEPS", "8"))
ALLOWED_TOOLS = {"run_query", "list_tables", "list_databases"}
MAX_TOOL_CHARS = 8000

langfuse = get_client()
llm = AsyncAnthropic(max_retries=6)


def get_prompt(label: str = "production", version: int | None = None):
    """Fetch the system prompt from Langfuse Prompt Management."""
    if version is not None:
        return langfuse.get_prompt(PROMPT_NAME, version=version)
    return langfuse.get_prompt(PROMPT_NAME, label=label)


def final_answer(text: str) -> str | None:
    found = re.findall(r"FINAL ANSWER:\s*(.+)", text or "", flags=re.IGNORECASE)
    return found[-1].strip() if found else None


def _blocks_to_dicts(content):
    """Minimal, API-safe copies of Claude's content blocks."""
    out = []
    for b in content:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


async def run_agent(question: str, *, user_id: str, session_id: str, prompt=None, tags=None) -> dict:
    """Answer one question. Returns {"answer", "final", "queries", "steps", "seconds"}."""
    prompt = prompt or get_prompt()
    system = prompt.compile()
    # Optional query budget from the prompt's Langfuse config, e.g. {"max_queries": 1}
    max_queries = (getattr(prompt, "config", None) or {}).get("max_queries")
    headers = {"Authorization": f"Bearer {MCP_TOKEN}"} if MCP_TOKEN else None
    started = time.perf_counter()

    with langfuse.start_as_current_observation(
        as_type="agent", name="uk-property-agent", input={"question": question}
    ) as root, propagate_attributes(
        user_id=user_id,
        session_id=session_id,
        tags=tags or [],
        trace_name="uk-property-agent",
        metadata={"prompt_version": str(prompt.version), "model": MODEL},
    ):
        async with streamablehttp_client(MCP_URL, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()
                listed = await mcp.list_tools()
                tools = [
                    {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
                    for t in listed.tools
                    if t.name in ALLOWED_TOOLS
                ]

                messages = [{"role": "user", "content": question}]
                answer, queries, steps = "", 0, 0

                for step in range(1, MAX_STEPS + 1):
                    steps = step
                    with langfuse.start_as_current_observation(
                        as_type="generation",
                        name=f"llm step {step}",
                        model=MODEL,
                        input={"system": system, "messages": messages},
                        prompt=prompt,
                    ) as gen:
                        out_of_budget = max_queries is not None and queries >= max_queries
                        resp = await llm.messages.create(
                            model=MODEL, max_tokens=1024, system=system, tools=tools, messages=messages,
                            tool_choice={"type": "none"} if out_of_budget else {"type": "auto"},
                        )
                        gen.update(
                            output=_blocks_to_dicts(resp.content),
                            usage_details={
                                "input": resp.usage.input_tokens,
                                "output": resp.usage.output_tokens,
                            },
                        )

                    messages.append({"role": "assistant", "content": _blocks_to_dicts(resp.content)})
                    answer = "".join(b.text for b in resp.content if b.type == "text")
                    if resp.stop_reason != "tool_use":
                        break

                    tool_results = []
                    for block in resp.content:
                        if block.type != "tool_use":
                            continue
                        if block.name == "run_query" and max_queries is not None and queries >= max_queries:
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": f"Query budget used ({max_queries}). Answer now with what you have.",
                                    "is_error": True,
                                }
                            )
                            continue
                        with langfuse.start_as_current_observation(
                            as_type="tool", name=f"clickhouse.{block.name}", input=block.input
                        ) as tool_span:
                            result = await mcp.call_tool(block.name, block.input)
                            text = "\n".join(
                                c.text for c in result.content if getattr(c, "type", "") == "text"
                            )[:MAX_TOOL_CHARS]
                            tool_span.update(
                                output=text, level="ERROR" if result.isError else "DEFAULT"
                            )
                        if block.name == "run_query":
                            queries += 1
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": text or "(no output)",
                                "is_error": bool(result.isError),
                            }
                        )
                    if max_queries is not None and queries >= max_queries:
                        tool_results.append(
                            {"type": "text", "text": "Query budget used. Reply now with your FINAL ANSWER line."}
                        )
                    messages.append({"role": "user", "content": tool_results})

        out = {
            "answer": answer,
            "final": final_answer(answer),
            "queries": queries,
            "steps": steps,
            "seconds": round(time.perf_counter() - started, 2),
        }
        root.update(output=out)
        return out


if __name__ == "__main__":
    import asyncio
    import sys

    q = " ".join(sys.argv[1:]) or "What was the average sale price in London in 2023?"
    res = asyncio.run(run_agent(q, user_id="tyler", session_id="cli-test"))
    print(res["answer"])
    print(f"\n[{res['queries']} queries, {res['steps']} steps, {res['seconds']}s]")
    langfuse.flush()
