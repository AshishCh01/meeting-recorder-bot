import statistics
import time
from typing import List

import requests


API_URL = "http://localhost:8000/meetings"
#API_URL = "http://localhost:8000/users/me"
# Paste your Supabase access token here.
# Do NOT commit this file to Git.
#ACCESS_TOKEN = 
ACCESS_TOKEN=""
WARMUP_REQUESTS = 20
BENCHMARK_REQUESTS = 200
TIMEOUT_SECONDS = 30


def percentile(values: List[float], p: float) -> float:
    """Return the nearest-rank percentile."""
    if not values:
        raise ValueError("No latency values available.")

    values = sorted(values)
    index = round((p / 100) * (len(values) - 1))
    return values[index]


def make_request(session: requests.Session) -> float:
    """Send one authenticated request and return latency in milliseconds."""
    start = time.perf_counter()

    response = session.get(
        API_URL,
        timeout=TIMEOUT_SECONDS,
    )

    elapsed_ms = (time.perf_counter() - start) * 1000

    response.raise_for_status()
    return elapsed_ms


def main() -> None:
    if ACCESS_TOKEN == "PASTE_YOUR_ACCESS_TOKEN_HERE":
        raise ValueError("Set ACCESS_TOKEN before running the benchmark.")

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Accept": "application/json",
    }

    with requests.Session() as session:
        session.headers.update(headers)

        print("Running warm-up requests...")

        for i in range(WARMUP_REQUESTS):
            try:
                make_request(session)
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Warm-up request {i + 1} failed: {exc}"
                ) from exc

        print(f"Warm-up complete. Running {BENCHMARK_REQUESTS} benchmark requests...\n")

        latencies: List[float] = []

        for i in range(BENCHMARK_REQUESTS):
            try:
                latency = make_request(session)
                latencies.append(latency)
            except requests.RequestException as exc:
                print(f"Request {i + 1} failed: {exc}")

        if not latencies:
            raise RuntimeError("All benchmark requests failed.")

    print("=" * 50)
    print("AUTHENTICATED API LATENCY BENCHMARK")
    print("=" * 50)
    print(f"Successful requests : {len(latencies)}")
    print(f"Failed requests     : {BENCHMARK_REQUESTS - len(latencies)}")
    print(f"Min                 : {min(latencies):.2f} ms")
    print(f"Mean                : {statistics.mean(latencies):.2f} ms")
    print(f"p50 (median)        : {percentile(latencies, 50):.2f} ms")
    print(f"p95                 : {percentile(latencies, 95):.2f} ms")
    print(f"p99                 : {percentile(latencies, 99):.2f} ms")
    print(f"Max                 : {max(latencies):.2f} ms")
    print("=" * 50)


if __name__ == "__main__":
    main()