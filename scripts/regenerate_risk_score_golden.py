"""Explicitly regenerate the baseline risk-score snapshot from a source checkout."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Source checkout to score")
    parser.add_argument("--output", type=Path, required=True, help="JSON snapshot output path")
    parser.add_argument("--source-commit", required=True, help="Commit represented by the source checkout")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not (repo / "model.py").is_file():
        parser.error("--repo must point to an InSighter checkout")

    with tempfile.TemporaryDirectory(prefix="insighter-risk-golden-") as directory:
        os.environ["INSIGHTER_DB_PATH"] = str(Path(directory) / "golden.db")
        os.environ["INSIGHTER_DISABLE_BACKGROUND_SIM"] = "1"
        os.environ["INSIGHTER_LLM_WARMUP"] = "0"
        os.environ["INSIGHTER_SECRET_KEY"] = "risk-golden-regeneration-only"
        sys.path.insert(0, str(repo))

        import database
        import model
        from auth import init_users_table
        from generate_logs import generate
        from sector_config import SECTORS

        database.ensure_database()
        init_users_table()
        snapshot = {
            "source_commit": args.source_commit,
            "generator_seed": 42,
            "sectors": {},
        }
        for sector in SECTORS:
            conn = database.connect()
            conn.execute(
                "UPDATE deployment_config SET sector=?, threshold_high=45, "
                "threshold_medium=30 WHERE id=1",
                (sector,),
            )
            conn.commit()
            conn.close()
            generate()
            snapshot["sectors"][sector] = [
                {
                    "user": row["user"],
                    "risk_score": row["risk_score"],
                    "risk_level": row["risk_level"],
                }
                for row in model.score_users()
            ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(snapshot, indent=2) + "\n")


if __name__ == "__main__":
    main()
