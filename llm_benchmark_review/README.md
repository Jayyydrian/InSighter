# LLM Benchmark Review

## Folders

- `benchmarks/`: standalone model benchmark scripts and the `llm_benchmark_*` results.
- `tests/`: the focused `test_llm_advisor.py` suite.
- `phi_cross_sector/`: Phi quality reviews and paired cross-sector outputs.

## Run

From the project root:

```powershell
python -m llm_benchmark_review.benchmarks.benchmark_llm_models
python -m llm_benchmark_review.benchmarks.benchmark_phi_sector_quality
python -m unittest discover -p "test_*.py"
```

The model benchmark writes CSV, JSON, and JSONL files into `benchmarks/` by default. The cross-sector runner writes its paired alert/output JSON into `phi_cross_sector/` by default. Both runners accept options to override their output paths.
