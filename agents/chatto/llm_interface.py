"""
LLM Interface module for communicating with the local LLM endpoint (LM Studio).

Handles prompt construction and API calling to the local language model server.
"""

import os
import httpx
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default endpoint
LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://127.0.0.1:1234")

# Skills file containing agent base instructions
SKILLS_FILE = Path(__file__).parent / "chatto_skills.md"


def load_skills() -> str:
    """Load Chatto base skills from markdown file."""
    if SKILLS_FILE.exists():
        return SKILLS_FILE.read_text()
    return ""


class LLMInterface:
    """Manages all communication with the local Language Model."""

    def __init__(self, lmstudio_url: Optional[str] = None):
        self.lmstudio_url = lmstudio_url or LMSTUDIO_URL
        self.client = httpx.Client()

    def query_llm(
        self,
        prompt: str,
        base_skills: str = "",
        tool_format: str = "",
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """
        Query LM Studio with conversation history and system prompts.

        Sends a request to the local LLM endpoint using the Chat Completions API format.
        Returns the raw text content from the model's response.
        """
        messages = [
            {"role": "system", "content": base_skills},
            {"role": "system", "content": tool_format},
        ]

        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": prompt})

        payload = {
            "messages": messages,
            "model": "gemma-4-e4b-it",
            "temperature": 0.7,
            "max_tokens": -1,
            "stream": False,
        }

        try:
            response = self.client.post(
                f"{self.lmstudio_url}/v1/chat/completions", json=payload, timeout=120
            )
            if response.status_code == 200:
                data = response.json()
                return (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "")
                )
            return f"Error: {response.text}"
        except Exception as e:
            return f"Error: {str(e)}"
