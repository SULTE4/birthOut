from __future__ import annotations

import json

import httpx

from birthout.models import BirthdayCandidate, NameSource


class OllamaError(RuntimeError):
    pass


class OllamaExtractor:
    def __init__(self, base_url: str, model: str = "qwen3:4b", timeout: float = 45.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def enrich(self, candidate: BirthdayCandidate) -> BirthdayCandidate:
        evidence = [
            {"timestamp": item.timestamp.isoformat(), "sender": item.sender, "text": item.excerpt}
            for item in candidate.evidence
        ]
        current = {"person": candidate.person, "month": candidate.month, "day": candidate.day, "year": candidate.year}
        prompt = (
            "Validate one possible birthday extracted from a WhatsApp chat. The text may be English, Russian, or Kazakh. "
            "Do not infer a birth year or identity that is not explicit. Return JSON only with keys valid, person, month, "
            "day, year, confidence, explanation.\n"
            f"Current candidate: {json.dumps(current, ensure_ascii=False)}\n"
            f"Evidence: {json.dumps(evidence, ensure_ascii=False)}"
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "stream": False,
                        "format": "json",
                        "messages": [{"role": "user", "content": prompt}],
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                result = json.loads(response.json()["message"]["content"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama could not validate the candidate: {exc}") from exc

        if not result.get("valid", True):
            candidate.confidence = min(candidate.confidence, 0.35)
            candidate.reason += "; local AI marked this candidate uncertain"
            return candidate
        try:
            month, day = int(result["month"]), int(result["day"])
            year = int(result["year"]) if result.get("year") else None
            if not 1 <= month <= 12 or not 1 <= day <= 31:
                raise ValueError("date outside supported range")
            proposed_person = str(result.get("person") or candidate.person).strip()[:60]
            if proposed_person != candidate.person:
                candidate.person = proposed_person
                candidate.name_source = NameSource.MESSAGE_TEXT
            candidate.month, candidate.day, candidate.year = month, day, year
            candidate.confidence = max(0.0, min(1.0, float(result.get("confidence", candidate.confidence))))
            explanation = str(result.get("explanation", "validated by local AI")).strip()[:200]
            candidate.reason += f"; Ollama: {explanation}"
        except (KeyError, TypeError, ValueError) as exc:
            raise OllamaError(f"Ollama returned an invalid birthday: {exc}") from exc
        return candidate
