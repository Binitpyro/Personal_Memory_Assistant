import json
import logging
from typing import Any

from app.api.deps import get_db, get_llm

logger = logging.getLogger(__name__)


async def generate_portrait() -> dict[str, Any]:
    """Generates a high-level Knowledge Portrait by clustering themes from folder profiles."""
    try:
        db = await get_db()
        llm = get_llm()

        # Get the compiled text of all folder profiles
        profiles_text = await db.get_folder_profiles_text()

        if not profiles_text or profiles_text.strip() == "":
            return {"themes": []}

        prompt = f"""You are an analytical engine exploring a user's local filesystem knowledge base.
Below are the summarized folder profiles representing the entire content of their database:

<PROFILES>
{profiles_text}
</PROFILES>

Analyze the overarching topics, themes, and areas of focus in these profiles.
Output a high-level "Knowledge Portrait" clustering the knowledge into 3 to 8 key themes.
For each theme, provide a 'name' (2-4 words), a brief 'description', and a 'weight' (an integer from 1 to 10 indicating prominence).

Provide ONLY valid JSON output matching this schema:
{{
  "themes": [
    {{
      "name": "Theme Name",
      "description": "Short description of what files belong here.",
      "weight": 8
    }}
  ]
}}
"""

        response = await llm.generate(prompt, temperature=0.2)

        # Parse the JSON response
        try:
            # Strip markdown formatting if any
            clean_text = response.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean_text)

            # isinstance on the container too, not just on themes: a model that
            # answers with a bare JSON array parses fine, and `"themes" in
            # parsed` then searches the list's *elements* rather than raising,
            # so the old check let a list reach the caller typed as a dict.
            if isinstance(parsed, dict) and isinstance(parsed.get("themes"), list):
                return parsed
            return {"themes": []}
        except json.JSONDecodeError as e:
            logger.error("Failed to parse portrait JSON from LLM: %s\nResponse: %s", e, response)
            return {"themes": []}

    except Exception as e:
        logger.error("Error generating knowledge portrait: %s", e)
        return {"themes": []}
