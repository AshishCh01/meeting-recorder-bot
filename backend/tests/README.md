# Backend tests

These run against a **real PostgreSQL**, not SQLite. The behaviour under test
in `test_scheduler_claim.py` is two connections racing a conditional `UPDATE`;
that depends on Postgres row-level locking and `READ COMMITTED` re-evaluation,
which SQLite does not reproduce (it serialises writers at the database level,
so the race cannot even be staged).

`conftest.py` reads `TEST_DATABASE_URL` and refuses to run without it, so a
stray `pytest` can never point the suite at the real `DATABASE_URL` in
`backend/.env`. It creates and drops the `users` and `meetings` tables, and
truncates them between tests -- use a throwaway database.

## Running

pytest lives in `requirements-dev.txt`, not `requirements.txt` -- the
production image (`backend/Dockerfile`) installs runtime deps only, so the
test runner never ships to the server. Install it on the host with:

```bash
pip install -r requirements-dev.txt
```

(The local docker-compose image, `backend/Dockerfile.dev`, already has it --
`docker compose exec backend python -m pytest` works without this step.)

Start a throwaway Postgres (non-default port, so it cannot collide with a
local instance):

```bash
docker run -d --rm --name meetiq-test-pg \
  -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=meetiq_test \
  -p 55432:5432 postgres:18
```

Then, from `backend/`:

```bash
TEST_DATABASE_URL=postgresql://postgres:testpw@localhost:55432/meetiq_test \
  python -m pytest
```

PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:testpw@localhost:55432/meetiq_test"
python -m pytest
```

Tear down with `docker stop meetiq-test-pg`.
