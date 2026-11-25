"""Extraction: turn unprocessed conversation into structured memories.

Uses the injected ``Model``; no client construction, no retries (the caller's
policy owns those).
"""

from __future__ import annotations

import json

from ..llm import ChatRequest, Model

_INSTRUCTIONS = """
Extract the following types of information:
1. **Facts**: Objective information learned (e.g., "User prefers VS Code editor")
2. **Decisions**: Choices made during conversation (e.g., "Decided to use PostgreSQL database")
3. **Solutions**: Problems solved or approaches identified
   (e.g., "Fixed bug by increasing timeout to 60s")
4. **Preferences**: User preferences detected (e.g., "User wants local-first approach")

For each extraction, provide:
- type: "fact", "decision", "solution", or "preference"
- content: The extracted information (as a dictionary with "text" key)
- confidence: 0.0 to 1.0 (how certain is this extraction)
- tags: List of relevant tags (e.g., ["configuration", "database"])

Return a JSON object with "extractions" array:
{{
  "extractions": [
    {{
      "type": "fact",
      "content": {{"text": "..."}},
      "confidence": 0.9,
      "tags": ["tag1", "tag2"]
    }}
  ]
}}

If nothing worth remembering, return:
{{"extractions": []}}

Only return valid JSON. No explanation."""

_EXTRACTION_PROMPT = """Analyze the following conversation and extract structured memories.

Conversation:
{conversation}
""" + _INSTRUCTIONS

_INCREMENTAL_PROMPT = """Analyze the new messages in this conversation and extract
structured memories.

Previously processed context (do NOT extract from this):
{old_conversation}

New messages (extract ONLY from these):
{new_conversation}
""" + _INSTRUCTIONS

_TYPES = {"fact", "decision", "solution", "preference"}


class MemoryExtractor:
    """Reads a formatted prompt from the store and asks the model for JSON."""

    def __init__(self, llm: Model, model: str, fallback: bool = True):
        self.llm = llm
        self.model = model
        self.fallback = fallback

    def extract(self, messages: list[dict]) -> list[dict]:
        if not messages:
            return []
        prompt = _EXTRACTION_PROMPT.format(conversation=self.format_conversation(messages))
        return self._extract_with_prompt(prompt)

    def extract_incremental(self, old_messages: list[dict], new_messages: list[dict]) -> list[dict]:
        if not new_messages:
            return []
        old_text = self.format_conversation(old_messages) or "(none; this is the first extraction)"
        prompt = _INCREMENTAL_PROMPT.format(
            old_conversation=old_text,
            new_conversation=self.format_conversation(new_messages),
        )
        return self._extract_with_prompt(prompt)

    def format_conversation(self, messages: list[dict]) -> str:
        return "\n".join(
            f"{str(message.get('role', 'unknown')).capitalize()}: {message.get('content', '')}"
            for message in messages
        )

    def _extract_with_prompt(self, prompt: str) -> list[dict]:
        try:
            reply = self.llm.complete(
                ChatRequest(
                    model=self.model,
                    messages=[{"role": "system", "content": prompt}],
                    temperature=0.3,
                    response_format={"type": "json_object"},
                )
            )
            result = json.loads(reply.text)
            return [
                extraction
                for extraction in result.get("extractions", [])
                if self._validate(extraction)
            ]
        except Exception:
            if self.fallback:
                return []
            raise

    def _validate(self, extraction: dict) -> bool:
        required = {"type", "content", "confidence", "tags"}
        if not required.issubset(extraction):
            return False
        if extraction["type"] not in _TYPES:
            return False
        if not isinstance(extraction["confidence"], (int, float)):
            return False
        if not 0 <= extraction["confidence"] <= 1:
            return False
        if not isinstance(extraction["tags"], list):
            return False
        return True
