"""Chunked adapter for the CERT Insider Threat Dataset r4.2."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import pandas as pd
import requests

DEFAULT_CHUNK_SIZE = 100_000
OFF_HOURS_START = 6
OFF_HOURS_END = 20
EXFIL_DOMAIN_WATCHLIST = {"wikileaks.org"}
JOB_SITE_KEYWORDS = ("indeed", "linkedin.com/jobs", "monster.com", "careerbuilder")
FEATURE_COLUMNS = [
    "user", "login_hour", "files_accessed", "data_transferred_mb",
    "failed_logins", "off_hours_access", "role", "removable_media_events",
    "exfil_domain_hits", "job_site_hits",
]
BASE_FEATURE_COLUMNS = FEATURE_COLUMNS[:-2]
PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_cert_paths(
    cert_dir: Path | str | None = None,
    answer_dir: Path | str | None = None,
) -> tuple[Path, Path]:
    """Resolve CERT paths from explicit arguments or required environment variables."""
    values = {
        "cert_dir": cert_dir or os.environ.get("INSIGHTER_CERT_DIR"),
        "answer_dir": answer_dir or os.environ.get("INSIGHTER_CERT_ANSWER_DIR"),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(
            "CERT dataset paths are not configured. Set "
            "--cert-dir/--answer-dir or the corresponding "
            "INSIGHTER_CERT_DIR/INSIGHTER_CERT_ANSWER_DIR environment variables."
        )

    resolved = {name: Path(value).expanduser().resolve() for name, value in values.items()}
    for name, path in resolved.items():
        if path == PROJECT_ROOT or PROJECT_ROOT in path.parents:
            option = "--cert-dir" if name == "cert_dir" else "--answer-dir"
            variable = "INSIGHTER_CERT_DIR" if name == "cert_dir" else "INSIGHTER_CERT_ANSWER_DIR"
            raise ValueError(
                f"{option}/{variable} points inside the project directory: {path}. "
                "CERT raw data and ground truth must stay outside the project directory "
                "to prevent multi-GB dataset files from being bundled into archives, "
                "commits, or IDE indexing."
            )
    return resolved["cert_dir"], resolved["answer_dir"]


def _normal(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _column(frame: pd.DataFrame, names: Iterable[str], required: bool = True) -> str | None:
    """Resolve a column despite harmless header spelling/case differences."""
    available = {_normal(column): column for column in frame.columns}
    for name in names:
        if _normal(name) in available:
            return available[_normal(name)]
    if required:
        raise ValueError(f"Missing one of {list(names)}; columns are {list(frame.columns)}")
    return None


def _header(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"CERT file not found: {path}")
    return pd.read_csv(path, nrows=0)


def _user_column(frame: pd.DataFrame) -> str:
    column = _column(frame, ("user", "username", "user_id", "userid", "employee"))
    assert column is not None
    return column


def _add_logons(path: Path, aggregates: dict[str, dict[str, Any]], chunk_size: int) -> None:
    """Aggregate logon hours incrementally and report processed rows."""
    header = _header(path)
    user_column = _user_column(header)
    timestamp_column = _column(header, ("date", "timestamp", "datetime", "time"))
    assert timestamp_column is not None
    processed = 0
    for chunk in pd.read_csv(path, chunksize=chunk_size, low_memory=False):
        hours = pd.to_datetime(chunk[timestamp_column], errors="coerce").dt.hour
        for username, hour in zip(chunk[user_column], hours):
            if pd.isna(username) or pd.isna(hour):
                continue
            user = str(username).strip()
            record = aggregates.setdefault(user, {"login_hours": [], "off_hours_access": 0})
            hour = int(hour)
            record["login_hours"].append(hour)
            if hour < OFF_HOURS_START or hour > OFF_HOURS_END:
                record["off_hours_access"] += 1
        processed += len(chunk)
        print(f"[CERT] logon.csv: processed {processed:,} rows")


def _add_files(path: Path, aggregates: dict[str, dict[str, Any]], chunk_size: int) -> None:
    """Count file events and removable-media transfers incrementally."""
    header = _header(path)
    user_column = _user_column(header)
    media_columns = [
        _column(header, ("to_removable_media", "toremovablemedia"), required=False),
        _column(header, ("from_removable_media", "fromremovablemedia"), required=False),
    ]
    media_columns = [column for column in media_columns if column is not None]
    processed = 0
    for chunk in pd.read_csv(path, chunksize=chunk_size, low_memory=False):
        for username, row in zip(chunk[user_column], chunk.to_dict(orient="records")):
            if pd.isna(username):
                continue
            user = str(username).strip()
            record = aggregates.setdefault(user, {})
            record["files_accessed"] = record.get("files_accessed", 0) + 1
            record["removable_media_events"] = record.get("removable_media_events", 0) + int(
                any(str(row.get(column, "")).strip().lower() in {"1", "true", "yes"} for column in media_columns)
            )
        processed += len(chunk)
        print(f"[CERT] file.csv: processed {processed:,} rows")


def _add_devices(path: Path, aggregates: dict[str, dict[str, Any]], chunk_size: int) -> None:
    """Count removable-device connection events incrementally."""
    header = _header(path)
    user_column = _user_column(header)
    activity_column = _column(header, ("activity", "action", "event"))
    assert activity_column is not None
    processed = 0
    for chunk in pd.read_csv(path, chunksize=chunk_size, low_memory=False):
        for username, activity in zip(chunk[user_column], chunk[activity_column]):
            if pd.isna(username):
                continue
            user = str(username).strip()
            record = aggregates.setdefault(user, {})
            if str(activity).strip().lower() == "connect":
                record["removable_media_events"] = record.get("removable_media_events", 0) + 1
        processed += len(chunk)
        print(f"[CERT] device.csv: processed {processed:,} rows")


def _add_email(path: Path, aggregates: dict[str, dict[str, Any]]) -> None:
    """Sum email attachment bytes; this is a transfer-volume proxy."""
    frame = pd.read_csv(path, low_memory=False)
    user_column = _user_column(frame)
    size_column = _column(frame, ("size", "attachment_size", "attachmentsize", "bytes"))
    assert size_column is not None
    for username, value in zip(frame[user_column], frame[size_column]):
        if pd.isna(username) or pd.isna(value):
            continue
        user = str(username).strip()
        aggregates.setdefault(user, {})["data_transferred_mb"] = (
            aggregates.setdefault(user, {}).get("data_transferred_mb", 0.0)
            + float(value) / (1024 * 1024)
        )
    print(f"[CERT] email.csv: processed {len(frame):,} rows")


def _add_http_signal(path: Path, aggregates: dict[str, dict[str, Any]], chunk_size: int) -> None:
    """Count watchlist/domain and job-site URL visits without reading content."""
    if not path.is_file():
        return
    header = _header(path)
    user_column = _user_column(header)
    url_column = _column(header, ("url", "uri", "link"))
    assert url_column is not None
    processed = 0
    for chunk in pd.read_csv(path, usecols=[user_column, url_column], chunksize=chunk_size, low_memory=False):
        for username, url in zip(chunk[user_column], chunk[url_column]):
            if pd.isna(username) or pd.isna(url):
                continue
            user = str(username).strip()
            normalized_url = str(url).strip().lower()
            record = aggregates.setdefault(user, {})
            record["exfil_domain_hits"] = record.get("exfil_domain_hits", 0) + int(
                any(domain in normalized_url for domain in EXFIL_DOMAIN_WATCHLIST)
            )
            record["job_site_hits"] = record.get("job_site_hits", 0) + int(
                any(keyword in normalized_url for keyword in JOB_SITE_KEYWORDS)
            )
        processed += len(chunk)
        print(f"[CERT] http.csv: processed {processed:,} rows")


def _load_roles(ldap_dir: Path, month: str | None = None) -> dict[str, str]:
    """Load the requested LDAP month, or the most recent month available."""
    files = sorted(
        (path for path in ldap_dir.rglob("*") if path.suffix.lower() in {".csv", ".tsv"}),
        key=lambda path: path.name,
    )
    if month:
        files = [path for path in files if month in path.name]
    if not files:
        raise FileNotFoundError(f"No LDAP file found below {ldap_dir}")
    path = files[-1]
    frame = pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",", low_memory=False)
    user_column = _user_column(frame)
    role_column = _column(frame, ("role", "job_role", "jobrole", "title", "employee_role"))
    assert role_column is not None
    roles = {
        str(user).strip(): str(role).strip()
        for user, role in zip(frame[user_column], frame[role_column])
        if pd.notna(user) and pd.notna(role) and str(role).strip()
    }
    print(f"[CERT] LDAP: loaded {len(roles):,} roles from {path.name}")
    return roles


def load_ground_truth(answer_dir: Path | str) -> pd.DataFrame:
    """Find and normalize an answer key to ``user, scenario, start, end``."""
    root = Path(answer_dir).expanduser()
    for path in sorted(root.rglob("*.csv")):
        frame = pd.read_csv(path, low_memory=False)
        columns = {
            name: _column(frame, aliases, required=False)
            for name, aliases in {
                "user": ("user", "username", "user_id"),
                "scenario": ("scenario", "scenario_id", "case"),
                "start": ("start", "start_date"),
                "end": ("end", "end_date"),
            }.items()
        }
        if all(columns.values()):
            result = frame[[columns[name] for name in ("user", "scenario", "start", "end")]].copy()
            result.columns = ["user", "scenario", "start", "end"]
            return result
    raise FileNotFoundError(f"No answer-key CSV found below {root}")


def build_feature_table(
    cert_dir: Path | str,
    ldap_dir: Path | str | None = None,
    *,
    ldap_month: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    include_http_signal: bool = True,
) -> pd.DataFrame:
    """Return one normalized feature row per CERT user."""
    root = Path(cert_dir).expanduser()
    ldap_root = Path(ldap_dir).expanduser() if ldap_dir else root / "LDAP"
    aggregates: dict[str, dict[str, Any]] = {}
    _add_logons(root / "logon.csv", aggregates, chunk_size)
    _add_files(root / "file.csv", aggregates, chunk_size)
    _add_devices(root / "device.csv", aggregates, chunk_size)
    _add_email(root / "email.csv", aggregates)
    if include_http_signal:
        _add_http_signal(root / "http.csv", aggregates, chunk_size)
    roles = _load_roles(ldap_root, ldap_month)
    rows = []
    for user, values in sorted(aggregates.items()):
        hours = values.get("login_hours", [])
        rows.append({
            "user": user,
            "login_hour": round(sum(hours) / len(hours), 1) if hours else 0,
            "files_accessed": int(values.get("files_accessed", 0)),
            "data_transferred_mb": round(float(values.get("data_transferred_mb", 0.0)), 3),
            "failed_logins": 0,
            "off_hours_access": int(values.get("off_hours_access", 0)),
            "role": roles.get(user, "unknown"),
            "removable_media_events": int(values.get("removable_media_events", 0)),
            "exfil_domain_hits": int(values.get("exfil_domain_hits", 0)),
            "job_site_hits": int(values.get("job_site_hits", 0)),
        })
    columns = FEATURE_COLUMNS if include_http_signal else BASE_FEATURE_COLUMNS
    result = pd.DataFrame(rows, columns=columns)
    print(f"[CERT] feature table: {len(result):,} users")
    return result


def feature_table_to_events(features: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert features to the event shape accepted by the ingestion contract."""
    required = [
        "user", "login_hour", "files_accessed", "data_transferred_mb",
        "failed_logins", "off_hours_access", "role",
    ]
    missing = [column for column in required if column not in features.columns]
    if missing:
        raise ValueError(f"Feature table missing ingestion columns: {missing}")
    return features[required].to_dict(orient="records")


def post_events(
    features: pd.DataFrame,
    endpoint: str | None = None,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Log in as an administrator and POST CERT events to ``/api/ingest``."""
    url = endpoint or os.environ.get("INSIGHTER_API_URL", "http://127.0.0.1:5000/api/ingest")
    username = os.environ.get("INSIGHTER_ADMIN_USER", "").strip()
    password = os.environ.get("INSIGHTER_ADMIN_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "Set INSIGHTER_ADMIN_USER and INSIGHTER_ADMIN_PASSWORD before CERT ingestion."
        )

    session = requests.Session()
    login_url = urljoin(url, "/login")
    try:
        login_response = session.post(
            login_url,
            data={"username": username, "password": password},
            allow_redirects=False,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Admin login could not reach {login_url}: {exc}") from exc

    if login_response.status_code != 302 or not session.cookies:
        raise RuntimeError(
            f"Admin login failed with HTTP {login_response.status_code}: "
            f"{login_response.text[:1000]}"
        )

    try:
        response = session.post(
            url,
            json={"events": feature_table_to_events(features)},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"CERT ingestion request could not reach {url}: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"CERT ingestion failed with HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"CERT ingestion returned HTTP 200 with invalid JSON: {response.text[:1000]}"
        ) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert-dir", type=Path)
    parser.add_argument("--answer-dir", type=Path)
    args = parser.parse_args()
    try:
        cert_dir, answer_dir = resolve_cert_paths(args.cert_dir, args.answer_dir)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"CERT directory: {cert_dir}")
    print(f"CERT answer directory: {answer_dir}")


if __name__ == "__main__":
    main()