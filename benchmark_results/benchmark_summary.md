# Local Small VLM Benchmark Results

| Model | Pass 1 ACS | Pass 1 Cell Acc | Pass 2 (With Python Loop) ACS | Pass 2 Cell Acc | Latency |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `openbmb/minicpm-v4.7:1b` | 0/20 | 0.0% | - | - | 22.1s |
| `qwen3-vl:2b` | 0/20 | 0.0% | - | - | 42.2s |
| `qwen3-vl:4b-instruct` | 19/20 | 98.33% | 19/20 | 98.33% | 98.2s |
| `qwen3-vl:8b` | 0/20 | 0.0% | - | - | 447.2s |
