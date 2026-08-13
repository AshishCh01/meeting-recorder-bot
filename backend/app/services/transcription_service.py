import json
import tempfile
import os

from google import genai

from app.config import settings
from app.db.supabase import supabase

client = genai.Client(api_key=settings.gemini_api_key)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "A concise 3-5 sentence overview of what the audio covers."
        },
        "key_points": {
            "type": "array",
            "description": "The main ideas, arguments, or options discussed, as short standalone bullets.",
            "items": {"type": "string"}
        },
        "action_items": {
            "type": "array",
            "description": "Concrete tasks, decisions, or follow-ups mentioned. Empty array if none.",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "The action or task itself."},
                    "owner": {
                        "type": "string",
                        "description": "Who is responsible, if stated or implied. Otherwise 'Unspecified'."
                    },
                    "timestamp": {"type": "string", "description": "MM:SS where this was mentioned."}
                },
                "required": ["item", "owner"]
            }
        },
        "conclusion": {
            "type": "string",
            "description": "How the conversation wraps up: final takeaway, resolution, or closing thought."
        },
        "conversation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Format: 'Speaker - text'. Use real name if mentioned in audio, otherwise 'Speaker 1', 'Speaker 2', etc."
                    },
                    "timestamp_start": {"type": "string", "description": "MM:SS format"},
                    "timestamp_end": {"type": "string", "description": "MM:SS format"}
                },
                "required": ["text", "timestamp_start", "timestamp_end"]
            }
        }
    },
    "required": ["summary", "key_points", "action_items", "conclusion", "conversation"]
}

PROMPT = """
Transcribe this audio in full, and also analyze its content.

Requirements for speaker identification:
1. Determine the number of distinct speakers.
2. Listen for names mentioned anywhere in the audio and reason carefully
   about WHO each name refers to:
   - If a speaker says "I'm X" or "This is X" -> the name belongs to the
     SPEAKER saying it.
   - If a speaker addresses someone directly by name (e.g. "Hi, Rohan",
     "Thanks, Priya", "Rohan, what do you think?")
     -> the name belongs to the OTHER speaker being spoken TO, not the one
     speaking.
   - If one speaker is greeted by name and then replies using a different
     name to address the first speaker back, use that exchange to assign
     both names correctly.
3. Once a speaker's real name is determined, use that name consistently
   for ALL of their segments in the transcript, including segments before
   the name was mentioned.
4. If no name is ever mentioned or the reference is ambiguous, fall back
   to "Speaker 1", "Speaker 2", etc. for that speaker only.
5. Provide accurate start/end timestamps for each segment (MM:SS format).
6. Detect the language of each segment individually.

Requirements for content analysis:
7. summary: A concise 3-5 sentence overview of the whole audio.
8. key_points: The main ideas, arguments, options, or topics discussed,
   as short standalone bullet points (not full transcript lines).
9. action_items: Any concrete tasks, decisions, or follow-ups mentioned,
   with an owner if one is stated or clearly implied by context (otherwise
   "Unspecified") and a timestamp of where it was mentioned. Return an
   empty array if the audio has no action items.
10. conclusion: How the conversation resolves or wraps up - the final
    takeaway, decision, or closing thought.
"""


def transcribe_recording(meeting_id: str, storage_path: str) -> dict:
    """
    Downloads the recording from Supabase Storage (NOT the database - the
    `meetings` table only ever holds a pointer/URL, never the audio bytes),
    runs it through Gemini, and writes the result onto the meeting row.
    """
    file_bytes = supabase.storage.from_(settings.supabase_recordings_bucket).download(storage_path)

    suffix = os.path.splitext(storage_path)[1] or ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        uploaded_file = client.files.upload(file=tmp_path, config={"mime_type": "audio/m4a"})

        interaction = client.interactions.create(
            model=settings.gemini_model,
            input=[
                {"type": "text", "text": PROMPT},
                {"type": "audio", "uri": uploaded_file.uri, "mime_type": "audio/m4a"}
            ],
            response_format=RESPONSE_SCHEMA,
        )

        result = json.loads(interaction.output_text)

        supabase.table("meetings").update({
            "transcript": result,
            "status": "completed",
        }).eq("id", meeting_id).execute()

        return result

    except Exception as e:
        supabase.table("meetings").update({
            "status": "failed",
            "error_message": f"Transcription failed: {e}",
        }).eq("id", meeting_id).execute()
        raise

    finally:
        os.unlink(tmp_path)