# Benchmarks

Reproducible scripts referenced from the README. Run from repo root:

```bash
python benchmarks/resume_savings.py   # resume vs naive restart step counts
python benchmarks/overhead.py           # checkpoint snapshot latency (local SQLite)
```

Optional dependency for future memory profiling: `pip install -e ".[benchmark]"`
