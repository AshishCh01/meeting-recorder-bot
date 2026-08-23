from app.config import settings


def gemini_generation_cost(prompt_tokens: int, output_tokens: int) -> float:
    """
    USD cost of a generate_content call, from config rates (settings.
    gemini_input_cost_per_mtok / gemini_output_cost_per_mtok), not hardcoded
    here, so pricing changes only require an env var update.
    """
    return (
        (prompt_tokens / 1_000_000) * settings.gemini_input_cost_per_mtok
        + (output_tokens / 1_000_000) * settings.gemini_output_cost_per_mtok
    )


def gemini_embedding_cost(token_count: int) -> float:
    return (token_count / 1_000_000) * settings.gemini_embedding_cost_per_mtok


def log_cost(operation: str, **fields) -> None:
    """
    Emits one greppable `[cost] <operation> k=v k=v ...` line. All cost
    logging across the app should go through this so the format stays
    consistent, e.g.:
    [cost] transcription meeting=<id> audio_sec=12.3 in_tok=100 out_tok=50 usd=0.000123
    """
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"[cost] {operation} {rendered}")
