"""Explicit paid smoke test: synthetic company/news, real bank excerpt, <=2 calls."""
import argparse
import asyncio
import json
from pathlib import Path

from app.agent.llm_client import LLMClient
from app.agent.service import run_analysis
from app.agent.state import AgentLimits
from app.agent.tools import JsonTools
from app.rag.ingest import atomic_json


async def check(output, analysis_only=False):
    async with LLMClient() as llm:
        selected = llm
        if analysis_only:
            # Explicit diagnostic mode: fixed offline plan, one real synthesis call.
            from app.agent.offline import OfflineLLM
            class FixedPlan:
                async def complete(self, messages, **kwargs):
                    stage = json.loads(messages[-1]["content"])["stage"]
                    if stage in {"plan", "replan"}:
                        return await OfflineLLM().complete(messages, **kwargs)
                    return await llm.complete(messages, **kwargs)
            selected = FixedPlan()
        tools = JsonTools(selected, company_id="DEMO_AUTO")
        try:
            run = await run_analysis("DEMO_AUTO", "demo-rates", tools,
                                     limits=AgentLimits(max_llm_calls=2, deadline_seconds=120,
                                                        plan_tokens=2000, analysis_tokens=6000))
        except Exception as exc:
            # Safe error type only; no provider body, key or raw prompts in the report.
            report = {"success": False, "error_type": type(exc).__name__,
                      "usage": getattr(exc, "usage", []), "synthetic_input": True}
        else:
            report = {"success": True, "usage": run.usage, "analysis": run.analysis,
                      "synthetic_input": True}
        report["fixed_offline_plan"] = analysis_only
        atomic_json(output, report)
        print(json.dumps({"success": report["success"], "calls": len(report["usage"]),
                          "report": str(output)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True, help="Explicitly spend API quota")
    parser.add_argument("--output", type=Path, default=Path(".dima-data/live-report.json"))
    parser.add_argument("--analysis-only", action="store_true", help="Fixed test plan and one real analysis call")
    args = parser.parse_args()
    asyncio.run(check(args.output, args.analysis_only))


if __name__ == "__main__":
    main()
