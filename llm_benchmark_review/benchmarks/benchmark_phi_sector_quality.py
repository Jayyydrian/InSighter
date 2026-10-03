"""Run Phi-4-mini against generated real alert outputs in two non-UAV sectors.

Run from the project root with: python -m llm_benchmark_review.benchmarks.benchmark_phi_sector_quality

The script seeds isolated temporary SQLite databases through generate_logs.py,
then retrieves alerts through model.get_recent_alerts(). It never imports app.py
or modifies the configured application database.
"""

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import requests

SECTORS = ("private_school", "sme_startup")
DEFAULT_ALERTS_PER_SECTOR = 10
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "phi_cross_sector"
    / "phi4_cross_sector_outputs.json"
)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alerts", type=int, default=DEFAULT_ALERTS_PER_SECTOR)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.alerts < 10:
        parser.error("--alerts must be at least 10")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return args


def _run_advisor(llm_advisor, alert, sector, sector_config):
    response_holder = {}
    original_post = llm_advisor.requests.post

    def capture_response(*args, **kwargs):
        response = original_post(*args, **kwargs)
        response_holder["response"] = response
        return response

    started = time.perf_counter()
    with patch.object(llm_advisor.requests, "post", new=capture_response):
        result = llm_advisor.explain_alert(alert, sector, sector_config)
    elapsed_seconds = time.perf_counter() - started

    raw_payload = {}
    response = response_holder.get("response")
    if response is not None:
        try:
            raw_payload = response.json()
        except (ValueError, TypeError, requests.RequestException):
            pass

    return {
        "model": llm_advisor.OLLAMA_MODEL,
        "sector": sector,
        "alert": alert,
        "success": result["available"],
        "reason": result["reason"],
        "explanation": result["explanation"],
        "recommendation": result["recommendation"],
        "generated_text": raw_payload.get("response"),
        "total_generation_time_seconds": elapsed_seconds,
        "prompt_eval_count": raw_payload.get("prompt_eval_count"),
        "eval_count": raw_payload.get("eval_count"),
    }


def main():
    args = _parse_args()
    os.environ["INSIGHTER_OLLAMA_TIMEOUT_SECONDS"] = str(args.timeout)
    os.environ["INSIGHTER_LLM_MODEL"] = "phi4-mini"

    import database
    import llm_advisor
    from generate_logs import generate
    from model import get_recent_alerts
    from sector_config import get_sector_config

    llm_advisor.OLLAMA_MODEL = "phi4-mini"
    llm_advisor.REQUEST_TIMEOUT_SECONDS = args.timeout
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    runs = []

    with tempfile.TemporaryDirectory(prefix="insighter-sector-benchmark-") as temp_dir:
        for sector in SECTORS:
            database.DB_PATH = os.path.join(temp_dir, f"{sector}.db")
            connection = database.connect()
            connection.execute(
                "CREATE TABLE deployment_config (id INTEGER PRIMARY KEY, sector TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO deployment_config (id, sector) VALUES (1, ?)", (sector,)
            )
            connection.commit()
            connection.close()

            generate()
            alerts = get_recent_alerts()
            if len(alerts) < args.alerts:
                raise RuntimeError(
                    f"Only {len(alerts)} alerts were generated for {sector}; "
                    f"{args.alerts} are required."
                )

            sector_config = get_sector_config(sector)
            selected_alerts = alerts[:args.alerts]
            print(f"{sector}: running {len(selected_alerts)} generated alerts")
            usage_path = os.path.join(temp_dir, f"{sector}_usage.jsonl")
            with patch.object(llm_advisor, "USAGE_LOG_PATH", usage_path):
                for index, alert in enumerate(selected_alerts, start=1):
                    run = _run_advisor(
                        llm_advisor, alert, sector, sector_config
                    )
                    runs.append(run)
                    status = "ok" if run["success"] else f"failed: {run['reason']}"
                    print(
                        f"  [{index}/{len(selected_alerts)}] alert {alert['id']}: "
                        f"{run['total_generation_time_seconds']:.2f}s, {status}"
                    )

    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump({
            "methodology": {
                "model": "phi4-mini",
                "sectors": list(SECTORS),
                "alerts_per_sector": args.alerts,
                "source": "Fresh isolated databases seeded with generate_logs.py; alerts retrieved with model.get_recent_alerts().",
                "application_database_modified": False,
            },
            "runs": runs,
        }, output_file, indent=2, ensure_ascii=False)
        output_file.write("\n")
    print(f"Saved paired alert inputs and outputs to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
