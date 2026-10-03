"""Compare local Ollama models using real alerts from the configured database.

Run from the project root with: python -m llm_benchmark_review.benchmarks.benchmark_llm_models
The advisor uses non-streaming generation, so recorded wall-clock latency is
full-response time, not time to first token. RAM values are RSS for the Ollama
server process that owns the configured API port, plus a separate RSS sum for
that process and its descendants (where model runner processes commonly live).
"""

import argparse
import csv
import json
import os
import statistics
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

import psutil
import requests

MODELS = ("llama3.1:8b", "qwen2.5:7b", "phi4-mini")
DEFAULT_ALERT_COUNT = 15
DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.1
DEFAULT_UNLOAD_WAIT_SECONDS = 2.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("INSIGHTER_OLLAMA_TIMEOUT_SECONDS", "180"))
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alerts",
        type=int,
        default=DEFAULT_ALERT_COUNT,
        help="Number of real alerts to use per model (10-20; default: 15).",
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=DEFAULT_SAMPLE_INTERVAL_SECONDS,
        help="Seconds between Ollama server RSS samples (default: 0.1).",
    )
    parser.add_argument(
        "--unload-wait",
        type=float,
        default=DEFAULT_UNLOAD_WAIT_SECONDS,
        help="Seconds to wait after requesting model unload (default: 2).",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        help="Maximum Ollama request time in seconds (default: 180; set higher for cold model loads).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for CSV, response JSON, and usage JSONL outputs (default: benchmarks folder).",
    )
    args = parser.parse_args()
    if not 10 <= args.alerts <= 20:
        parser.error("--alerts must be between 10 and 20")
    if args.sample_interval <= 0:
        parser.error("--sample-interval must be greater than zero")
    if args.unload_wait < 0:
        parser.error("--unload-wait cannot be negative")
    if args.request_timeout <= 0:
        parser.error("--request-timeout must be greater than zero")
    return args


def _ollama_port(host):
    parsed = urlparse(host)
    return parsed.port or (443 if parsed.scheme == "https" else 11434)


def _is_ollama_process(process):
    try:
        name = (process.name() or "").lower()
        command_line = " ".join(process.cmdline()).lower()
    except (psutil.Error, OSError):
        return False
    return "ollama" in name or "ollama" in command_line


def find_ollama_server_pid(port):
    """Find the Ollama process listening on the configured API port."""
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        connections = []

    for connection in connections:
        local_address = connection.laddr
        local_port = getattr(local_address, "port", None)
        if local_port is None and local_address:
            try:
                local_port = local_address[1]
            except (IndexError, TypeError):
                pass
        if (
            connection.status == psutil.CONN_LISTEN
            and local_port == port
            and connection.pid is not None
        ):
            try:
                process = psutil.Process(connection.pid)
            except (psutil.Error, OSError):
                continue
            if _is_ollama_process(process):
                return process.pid

    candidates = []
    try:
        processes = psutil.process_iter(attrs=["pid", "name", "cmdline"])
        for process in processes:
            info = process.info
            name = (info.get("name") or "").lower()
            command_line = " ".join(info.get("cmdline") or []).lower()
            if "ollama" not in name and "ollama" not in command_line:
                continue
            server_rank = 0 if "serve" in command_line else 1
            runner_rank = 1 if "runner" in command_line else 0
            candidates.append((server_rank, runner_rank, info["pid"]))
    except (psutil.Error, OSError):
        pass

    if candidates:
        candidates.sort()
        return candidates[0][2]
    return None


class RssSampler:
    """Sample RSS for one Ollama server process while a request is in flight."""

    def __init__(self, pid, interval_seconds):
        self.pid = pid
        self.interval_seconds = interval_seconds
        self.server_before_bytes = None
        self.server_peak_bytes = None
        self.process_tree_before_bytes = None
        self.process_tree_peak_bytes = None
        self.sample_count = 0
        self.error = None
        self._stop_event = threading.Event()
        self._thread = None

    def _sample(self, baseline=False):
        if self.pid is None:
            self.error = "Ollama server process was not found"
            return
        try:
            server_process = psutil.Process(self.pid)
            server_rss = server_process.memory_info().rss
        except (psutil.Error, OSError) as exc:
            self.error = type(exc).__name__
            return

        process_tree_rss = server_rss
        try:
            descendants = server_process.children(recursive=True)
        except (psutil.Error, OSError):
            descendants = []
        for process in descendants:
            try:
                process_tree_rss += process.memory_info().rss
            except (psutil.Error, OSError):
                continue

        if baseline:
            self.server_before_bytes = server_rss
            self.process_tree_before_bytes = process_tree_rss
        self.server_peak_bytes = max(self.server_peak_bytes or server_rss, server_rss)
        self.process_tree_peak_bytes = max(
            self.process_tree_peak_bytes or process_tree_rss, process_tree_rss
        )
        self.sample_count += 1

    def _sample_loop(self):
        while not self._stop_event.wait(self.interval_seconds):
            self._sample()

    def start(self):
        self._sample(baseline=True)
        if self.pid is not None:
            self._thread = threading.Thread(target=self._sample_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
        self._sample()

    @property
    def server_delta_bytes(self):
        if self.server_before_bytes is None or self.server_peak_bytes is None:
            return None
        return self.server_peak_bytes - self.server_before_bytes

    @property
    def process_tree_delta_bytes(self):
        if self.process_tree_before_bytes is None or self.process_tree_peak_bytes is None:
            return None
        return self.process_tree_peak_bytes - self.process_tree_before_bytes


def _file_size(path):
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _read_usage_entry(path, offset):
    try:
        with path.open("rb") as log_file:
            log_file.seek(offset)
            lines = [line for line in log_file if line.strip()]
    except OSError:
        return {}
    if not lines:
        return {}
    try:
        return json.loads(lines[-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _invoke_explanation(llm_advisor, alert, sector, sector_config):
    """Use explain_alert unchanged while retaining its raw Ollama response."""
    response_holder = {}
    original_post = llm_advisor.requests.post

    def capture_response(*args, **kwargs):
        response = original_post(*args, **kwargs)
        response_holder["response"] = response
        return response

    error = None
    result = None
    with patch.object(llm_advisor.requests, "post", new=capture_response):
        try:
            result = llm_advisor.explain_alert(alert, sector, sector_config)
        except Exception as exc:
            error = exc

    generated_text = None
    response = response_holder.get("response")
    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                generated_text = payload.get("response")
        except (ValueError, TypeError, requests.RequestException):
            pass
    return result, generated_text, error


def _benchmark_call(llm_advisor, alert, sector, sector_config, usage_log_path, sample_interval):
    usage_offset = _file_size(usage_log_path)
    server_pid = find_ollama_server_pid(_ollama_port(llm_advisor.OLLAMA_HOST))
    sampler = RssSampler(server_pid, sample_interval)
    sampler.start()

    started = time.perf_counter()
    result, generated_text, call_error = _invoke_explanation(
        llm_advisor, alert, sector, sector_config
    )
    generation_seconds = time.perf_counter() - started
    sampler.stop()

    usage = _read_usage_entry(usage_log_path, usage_offset)
    success = bool(result and result.get("available"))
    error_type = usage.get("error_type")
    if call_error is not None:
        error_type = type(call_error).__name__
    elif not success and not error_type:
        error_type = "UnknownError"

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "alert_id": alert.get("id"),
        "model": llm_advisor.OLLAMA_MODEL,
        "wall_clock_measurement": "full_response_non_streaming",
        "wall_clock_time_to_first_token_seconds": None,
        "total_generation_time_seconds": generation_seconds,
        "prompt_eval_count": usage.get("prompt_eval_count"),
        "eval_count": usage.get("eval_count"),
        "success": success,
        "error_type": error_type,
        "reason": result.get("reason") if result else str(call_error),
        "generated_text": generated_text,
        "explanation": result.get("explanation") if result else None,
        "recommendation": result.get("recommendation") if result else None,
        "ollama_server_pid": server_pid,
        "ollama_server_rss_before_bytes": sampler.server_before_bytes,
        "ollama_server_rss_peak_bytes": sampler.server_peak_bytes,
        "ollama_server_rss_delta_bytes": sampler.server_delta_bytes,
        "ollama_process_tree_rss_before_bytes": sampler.process_tree_before_bytes,
        "ollama_process_tree_rss_peak_bytes": sampler.process_tree_peak_bytes,
        "ollama_process_tree_rss_delta_bytes": sampler.process_tree_delta_bytes,
        "ollama_server_rss_error": sampler.error,
        "ram_sample_count": sampler.sample_count,
    }


def _mean(values):
    available = [value for value in values if isinstance(value, (int, float))]
    return statistics.mean(available) if available else None


def _median(values):
    available = [value for value in values if isinstance(value, (int, float))]
    return statistics.median(available) if available else None


def _build_summary(runs, total_alerts):
    summary = []
    for model in MODELS:
        model_runs = [run for run in runs if run["model"] == model]
        successful_count = sum(run["success"] for run in model_runs)
        ram_delta_mb = [
            run["ollama_process_tree_rss_delta_bytes"] / (1024 * 1024)
            if run["ollama_process_tree_rss_delta_bytes"] is not None else None
            for run in model_runs
        ]
        server_ram_delta_mb = [
            run["ollama_server_rss_delta_bytes"] / (1024 * 1024)
            if run["ollama_server_rss_delta_bytes"] is not None else None
            for run in model_runs
        ]
        summary.append({
            "model": model,
            "alerts_tested": len(model_runs),
            "success_count": successful_count,
            "success_rate_percent": successful_count / total_alerts * 100 if total_alerts else 0,
            "mean_generation_time_seconds": _mean(
                [run["total_generation_time_seconds"] for run in model_runs]
            ),
            "median_generation_time_seconds": _median(
                [run["total_generation_time_seconds"] for run in model_runs]
            ),
            "mean_ram_delta_mb": _mean(ram_delta_mb),
            "median_ram_delta_mb": _median(ram_delta_mb),
            "mean_server_process_rss_delta_mb": _mean(server_ram_delta_mb),
            "median_server_process_rss_delta_mb": _median(server_ram_delta_mb),
            "mean_prompt_eval_count": _mean(
                [run["prompt_eval_count"] for run in model_runs]
            ),
            "mean_eval_count": _mean([run["eval_count"] for run in model_runs]),
        })
    return summary


def _write_summary_csv(path, summary):
    fieldnames = list(summary[0]) if summary else []
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)


def _print_summary(summary):
    headers = (
        "Model", "Tested", "Success", "Generation mean / median (s)",
        "RAM delta mean / median (MiB, server + children)",
        "Server RSS delta mean / median (MiB)", "Input tokens mean", "Output tokens mean",
    )
    rows = []
    for item in summary:
        rows.append((
            item["model"],
            str(item["alerts_tested"]),
            f"{item['success_rate_percent']:.1f}% ({item['success_count']}/{item['alerts_tested']})",
            _format_pair(item["mean_generation_time_seconds"], item["median_generation_time_seconds"]),
            _format_pair(item["mean_ram_delta_mb"], item["median_ram_delta_mb"]),
            _format_pair(
                item["mean_server_process_rss_delta_mb"],
                item["median_server_process_rss_delta_mb"],
            ),
            _format_value(item["mean_prompt_eval_count"]),
            _format_value(item["mean_eval_count"]),
        ))
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(len(headers))]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _format_value(value):
    return "n/a" if value is None else f"{value:.2f}"


def _format_pair(mean, median):
    return f"{_format_value(mean)} / {_format_value(median)}"


def _unload_model(host, model, wait_seconds):
    try:
        response = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": "", "stream": False, "keep_alive": 0},
            timeout=30,
        )
        response.raise_for_status()
        print(f"Unloaded {model}.")
    except requests.RequestException as exc:
        print(f"Warning: could not explicitly unload {model}: {type(exc).__name__}: {exc}")
    if wait_seconds:
        time.sleep(wait_seconds)


def main():
    args = _parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    usage_log_path = output_dir / "llm_benchmark_usage.jsonl"

    # Keep benchmark measurements separate from the application's regular usage log.
    os.environ["INSIGHTER_LLM_USAGE_LOG_PATH"] = str(usage_log_path)
    os.environ["INSIGHTER_OLLAMA_TIMEOUT_SECONDS"] = str(args.request_timeout)
    import llm_advisor
    from model import get_recent_alerts
    from sector_config import DEFAULT_SECTOR, get_active_sector, get_sector_config

    sector = get_active_sector() or DEFAULT_SECTOR
    sector_config = get_sector_config(sector)
    alerts = get_recent_alerts()
    if len(alerts) < 10:
        print(
            f"Found only {len(alerts)} real alerts in the configured database; "
            "at least 10 are required. No alerts were fabricated.",
            file=os.sys.stderr,
        )
        return 1
    alerts = alerts[:args.alerts]
    print(
        f"Benchmarking {len(alerts)} real alerts in sector '{sector}' across {len(MODELS)} models."
    )
    print("Timing is full-response wall-clock latency; this non-streaming path does not measure first-token time.")
    print(f"Ollama server RSS is sampled every {args.sample_interval:g}s.")

    runs = []
    for model in MODELS:
        os.environ["INSIGHTER_LLM_MODEL"] = model
        llm_advisor.OLLAMA_MODEL = os.environ["INSIGHTER_LLM_MODEL"]
        print(f"\nModel: {model}")
        for index, alert in enumerate(alerts, start=1):
            run = _benchmark_call(
                llm_advisor,
                alert,
                sector,
                sector_config,
                usage_log_path,
                args.sample_interval,
            )
            runs.append(run)
            status = "ok" if run["success"] else f"failed ({run['error_type']})"
            print(
                f"  [{index}/{len(alerts)}] alert {run['alert_id']}: "
                f"{run['total_generation_time_seconds']:.2f}s, {status}"
            )
        _unload_model(llm_advisor.OLLAMA_HOST, model, args.unload_wait)

    summary = _build_summary(runs, len(alerts))
    csv_path = output_dir / "llm_benchmark_summary.csv"
    outputs_path = output_dir / "llm_benchmark_outputs.json"
    _write_summary_csv(csv_path, summary)
    with outputs_path.open("w", encoding="utf-8") as output_file:
        json.dump({
            "measurement_notes": {
                "wall_clock": "Full-response latency from explain_alert(); non-streaming, so time to first token is not measured.",
                "ram": "Records API-listener process RSS and RSS summed across that process plus descendants; summary RAM delta uses the latter so child model runners are included when present.",
                "alerts": "Retrieved from model.get_recent_alerts() using the configured database; no alert fixtures are generated.",
            },
            "runs": runs,
        }, output_file, indent=2, ensure_ascii=False)
        output_file.write("\n")

    print("\nSummary")
    _print_summary(summary)
    print(f"\nCSV: {csv_path}")
    print(f"Individual outputs: {outputs_path}")
    print(f"Usage metrics: {usage_log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
