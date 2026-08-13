"""
Test the transcription pipeline directly against a file in Supabase Storage,
without requiring a matching row in the `meetings` table.

Usage:
    cd backend
    python -m scripts.test_transcription test-023/recording.m4a
"""
import sys
import json
import tempfile
import os

from google import genai

from app.config import settings
from app.db.supabase import supabase
from app.services.transcription_service import PROMPT, RESPONSE_SCHEMA

client = genai.Client(api_key=settings.gemini_api_key)


def main(storage_path: str):
    print(f"Downloading '{storage_path}' from bucket '{settings.supabase_recordings_bucket}'...")
    file_bytes = supabase.storage.from_(settings.supabase_recordings_bucket).download(storage_path)

    suffix = os.path.splitext(storage_path)[1] or ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        print("Uploading to Gemini...")
        uploaded_file = client.files.upload(file=tmp_path, config={"mime_type": "audio/m4a"})

        print("Running transcription...")
        interaction = client.interactions.create(
            model=settings.gemini_model,
            input=[
                {"type": "text", "text": PROMPT},
                {"type": "audio", "uri": uploaded_file.uri, "mime_type": "audio/m4a"}
            ],
            response_format=RESPONSE_SCHEMA,
        )

        result = json.loads(interaction.output_text)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.test_transcription <storage_path>")
        sys.exit(1)
    main(sys.argv[1])