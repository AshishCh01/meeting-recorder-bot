"""
Authenticated-request latency benchmark for docs/scaling-plan.md Phase A5.

Measures what a client on this machine actually experiences, which is a
different (and more honest) number than measuring from inside the container:
requests here cross Docker Desktop's port proxy the same way a browser's do.

The token is NEVER stored in this file. It is a live credential and this file
is tracked in git - a pasted token here is one `git add -A` away from being in
history permanently. It is read, in order, from:

    1. --token on the command line
    2. $BENCH_ACCESS_TOKEN
    3. backend/.env.local, key A5_BENCHMARK_TOKEN or ACCESS_TOKEN

`.env.local` is gitignored, which is why it is the default home for it.

Usage
-----
    python benchmark_auth.py                      # all endpoints, serial
    python benchmark_auth.py -n 200 -w 20         # sample sizes
    python benchmark_auth.py -c 8                 # 8 concurrent clients
    python benchmark_auth.py --endpoints /users/me
    python benchmark_auth.py --label "local JWT + cache"

What it measures
----------------
1. Per-endpoint authenticated latency, with the first (cold) request reported
   separately - it is usually an outlier and averaging it in hides the steady
   state.
2. The rejection path: an invalid token, timed. Under A5 this should be local
   and fast; if the fallback wrapper is reached it costs an HTTPS round-trip to
   Supabase, which shows up here as a much slower 401.
3. Optional concurrency, because serial timing cannot show queueing.
"""
import argparse
import base64
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

import requests

BASE_URL = os.environ.get("BENCH_BASE_URL", "http://localhost:8000")
DEFAULT_ENDPOINTS = ["/users/me", "/meetings"]
ENV_LOCAL = Path(__file__).with_name(".env.local")
TIMEOUT_SECONDS = 30

# --------------------------------------------------------------------------

def _from_env_local() -> Optional[str]:
    if not ENV_LOCAL.exists():
        return None
    for line in ENV_LOCAL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in ("A5_BENCHMARK_TOKEN", "ACCESS_TOKEN"):
            value = value.strip().strip('"').strip("'")
            if value:
                return value
    return None


def load_token(cli_token: Optional[str]) -> str:
    token = cli_token or os.environ.get("BENCH_ACCESS_TOKEN") or _from_env_local()
    if not token:
        sys.exit(
            "No access token.\n\n"
            "Put a fresh Supabase access token in backend/.env.local (gitignored):\n"
            "    A5_BENCHMARK_TOKEN=eyJ...\n\n"
            "or pass --token / set $BENCH_ACCESS_TOKEN.\n"
            "Do not paste it into this file - it is tracked in git."
        )
    return token


def describe_token(token: str) -> Tuple[bool, str]:
    """(is_usable, human description). Catches the expired-token case early."""
    try:
        header_b64, payload_b64 = token.split(".")[:2]
        pad = lambda s: s + "=" * (-len(s) % 4)
        header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
        claims = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
    except Exception as exc:
        return False, f"could not decode token: {exc}"

    exp = claims.get("exp")
    now = int(time.time())
    alg = header.get("alg")
    sub = str(claims.get("sub", "?"))[:8]

    if exp is None:
        return True, f"alg={alg} sub={sub}... (no exp claim)"
    remaining = exp - now
    if remaining <= 0:
        return False, (
            f"alg={alg} sub={sub}... EXPIRED {abs(remaining)//60} min ago.\n"
            "Every request would 401 and the numbers would be meaningless.\n"
            "Grab a fresh token and put it in backend/.env.local."
        )
    return True, f"alg={alg} sub={sub}... valid for {remaining // 60} more min"


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------

def percentile(values: List[float], p: float) -> float:
    values = sorted(values)
    return values[round((p / 100) * (len(values) - 1))]


def timed_get(session: requests.Session, url: str, token: str) -> Tuple[float, int]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    start = time.perf_counter()
    response = session.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, response.status_code


def run_serial(url: str, token: str, warmup: int, count: int, expect: int):
    cold = None
    latencies: List[float] = []
    statuses: dict = {}

    with requests.Session() as session:
        for i in range(warmup):
            elapsed, status = timed_get(session, url, token)
            if i == 0:
                cold = elapsed
            statuses[status] = statuses.get(status, 0) + 1
        for _ in range(count):
            elapsed, status = timed_get(session, url, token)
            statuses[status] = statuses.get(status, 0) + 1
            if status == expect:
                latencies.append(elapsed)

    return cold, latencies, statuses


def run_concurrent(url: str, token: str, count: int, workers: int, expect: int):
    latencies: List[float] = []
    statuses: dict = {}

    def one(_):
        # A session per worker: connection reuse within a worker, which is how
        # a real client behaves. Sharing one Session across threads would
        # serialise on its connection pool and measure the wrong thing.
        with requests.Session() as session:
            return timed_get(session, url, token)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for elapsed, status in pool.map(one, range(count)):
            statuses[status] = statuses.get(status, 0) + 1
            if status == expect:
                latencies.append(elapsed)
    wall = time.perf_counter() - started
    return latencies, statuses, wall


def report(title: str, latencies: List[float], statuses: dict, cold=None, wall=None):
    print("-" * 62)
    print(title)
    print("-" * 62)
    if not latencies:
        print(f"  no successful samples. status codes: {statuses}")
        return
    print(f"  samples   : {len(latencies)}    status codes: {statuses}")
    if cold is not None:
        print(f"  cold (1st): {cold:8.2f} ms")
    print(f"  min       : {min(latencies):8.2f} ms")
    print(f"  mean      : {statistics.mean(latencies):8.2f} ms")
    print(f"  p50       : {percentile(latencies, 50):8.2f} ms")
    print(f"  p90       : {percentile(latencies, 90):8.2f} ms")
    print(f"  p95       : {percentile(latencies, 95):8.2f} ms")
    print(f"  p99       : {percentile(latencies, 99):8.2f} ms")
    print(f"  max       : {max(latencies):8.2f} ms")
    if wall is not None:
        print(f"  wall      : {wall:8.2f} s  ({len(latencies) / wall:.1f} req/s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--requests", type=int, default=200)
    parser.add_argument("-w", "--warmup", type=int, default=20)
    parser.add_argument("-c", "--concurrency", type=int, default=0,
                        help="also run a concurrent pass with this many workers")
    parser.add_argument("--endpoints", nargs="*", default=DEFAULT_ENDPOINTS)
    parser.add_argument("--token", default=None)
    parser.add_argument("--label", default="", help="tag for this configuration")
    parser.add_argument("--skip-reject", action="store_true")
    parser.add_argument("--reject-only", action="store_true",
                        help="only time the 401 path; needs no valid token")
    args = parser.parse_args()

    if args.reject_only:
        token, usable, description = "", True, "not needed (--reject-only)"
    else:
        token = load_token(args.token)
        usable, description = describe_token(token)
    print("=" * 62)
    print("AUTHENTICATED API LATENCY BENCHMARK" + (f"  [{args.label}]" if args.label else ""))
    print("=" * 62)
    print(f"base url : {BASE_URL}")
    print(f"token    : {description}")
    if not usable:
        sys.exit(1)
    print(f"plan     : {args.warmup} warm-up + {args.requests} timed per endpoint")
    print()

    for path in ([] if args.reject_only else args.endpoints):
        url = BASE_URL + path
        cold, latencies, statuses = run_serial(url, token, args.warmup, args.requests, 200)
        report(f"{path}  (serial, authenticated)", latencies, statuses, cold=cold)

        if args.concurrency:
            lat, st, wall = run_concurrent(url, token, args.requests, args.concurrency, 200)
            report(f"{path}  (concurrent x{args.concurrency})", lat, st, wall=wall)
        print()

    if not args.skip_reject:
        # The rejection path. Under local verification this never leaves the
        # process; if the fallback wrapper is reached it costs a round-trip to
        # Supabase, and the difference is visible right here.
        url = BASE_URL + args.endpoints[0]
        _, lat, st = run_serial(url, INVALID_TOKEN, 5, min(args.requests, 50), 401)
        report(f"{args.endpoints[0]}  (invalid token -> 401)", lat, st)

    print()
    print("=" * 62)


if __name__ == "__main__":
    main()
