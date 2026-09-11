"""
The test process sees code defaults plus explicit test values - never a .env.

backend/.env used to reach the suite by two independent paths: load_dotenv() in
app/db/database.py copied every key into os.environ, and Settings' env_file
read the file directly. So results depended on whose machine ran the suite (a
queue flag flipped in someone's .env failed four scheduler tests), and real
provider keys sat in the test process, one unstubbed fallback away from a
billed call.

"Explicit" means one of two things: conftest.py set it (the stub database URL
and placeholder credentials), or it was already in the environment of whoever
ran pytest - conftest.RUNNER_ENV_KEYS, captured before any app.* import. That
second allowance is what lets a flag be overridden on purpose from a shell or
CI. It does not extend to credentials: those must sit at their code default
whoever set them.

Every failure message lists names only. Never put a value, os.environ itself,
or settings into an assertion or a print: pytest renders the operands of a
failed assert, and repr(settings) contains every key it holds.
"""
import os

from app.config import Settings, settings

# Credentials, or config that points at a live third party. A test that needs
# one of these must stub the call, not borrow a key.
SECRET_FIELDS = frozenset({
    "sarvam_api_key",
    "jina_api_key",
    "groq_api_key",
    "google_client_id",
    "google_client_secret",
    "google_token_encryption_key",
    "sentry_dsn",
})

# Set by pytest itself while a test runs, not by the application.
SET_BY_PYTEST = frozenset({"PYTEST_CURRENT_TEST"})


def test_every_setting_is_its_code_default_unless_set_on_purpose(runner_env_keys, conftest_env_keys):
    offending = []
    for name, field in Settings.model_fields.items():
        env_name = name.upper()
        if env_name in conftest_env_keys:
            continue
        if getattr(settings, name) == field.get_default(call_default_factory=True):
            continue
        if env_name in runner_env_keys and name not in SECRET_FIELDS:
            continue
        offending.append(env_name)

    assert not offending, (
        f"{len(offending)} setting(s) differ from their code default without being set by "
        f"conftest.py or by whoever ran pytest (names only): {', '.join(sorted(offending))}"
    )


def test_no_key_appears_in_os_environ_that_nobody_set_on_purpose(runner_env_keys, conftest_env_keys):
    leaked = sorted(
        key for key in os.environ
        if key not in runner_env_keys and key not in conftest_env_keys and key not in SET_BY_PYTEST
    )

    assert not leaked, (
        f"{len(leaked)} environment variable(s) appeared in the test process that neither "
        f"conftest.py nor whoever ran pytest set (names only): {', '.join(leaked)}"
    )
