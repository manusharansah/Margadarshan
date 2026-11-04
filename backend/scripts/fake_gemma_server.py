"""
TEST DOUBLE - not a model. Speaks Google AI Studio's generateContent wire format so the real
GemmaClient (HTTP, JSON-schema config, parsing) and the UI can be exercised end to end without an API key.
Its answers come from regexes, so it proves wiring and guardrails, NOT language quality.

    python scripts/fake_gemma_server.py   # :9100
    GEMMA_API_KEY=fake GEMMA_ENDPOINT='http://127.0.0.1:9100/models/{model}:generateContent' uvicorn ...
"""
import datetime as dt
import json
import re

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()


def reply(obj: dict) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}


@app.post("/models/{action}")
async def generate(action: str, request: Request):
    prompt = (await request.json())["contents"][0]["parts"][0]["text"]
    if "Extract a delivery order" in prompt:
        msg = prompt.split("Message: ", 1)[1]
        qty = re.search(r"(\d+)\s*(?:units|pcs|bags)?", msg)
        dest = re.search(r"\bto ([A-Z][A-Za-z]+)", msg)
        hrs = re.search(r"in (\d+(?:\.\d+)?)\s*(?:h|hours?)", msg)
        iso = None
        if "tomorrow 5" in msg:
            now = dt.datetime.fromisoformat(re.search(r"Current Nepal time: (\S+)", prompt).group(1))
            iso = (now + dt.timedelta(days=1)).replace(hour=17, minute=0).strftime("%Y-%m-%dT%H:%M")
        return reply({"destination_text": dest.group(1) if dest else None, "quantity": int(qty.group(1)) if qty else None,
                      "deadline_hours": float(hrs.group(1)) if hrs else None, "deadline_local_iso": iso, "notes": "[test double]"})
    if "Propose up to 3" in prompt:
        facts = json.loads(prompt.split("Facts: ", 1)[1].split("\nRespond", 1)[0])
        q, h = facts["order"]["quantity"], facts["order"]["deadline_hours"]
        return reply({"proposals": [
            {"quantity": max(1, q // 3), "deadline_hours": max(h, 24), "rationale": "[test double] Ship a third now, the rest in a later run."},
            {"quantity": q, "deadline_hours": 0.001, "rationale": "[test double] impossible - must be rejected by the planner."}]})
    if "No plan meets the deadline" in prompt:
        return reply({"chosen_index": None, "explanation": "[test double] Not deliverable as requested.", "risk_note": ""})
    return reply({"chosen_index": 0, "explanation": "[test double] Option 0 is simplest and within the deadline.",
                  "risk_note": "[test double] Take care on the listed riskiest stretches."})


if __name__ == "__main__":
    uvicorn.run(app, port=9100, log_level="warning")
