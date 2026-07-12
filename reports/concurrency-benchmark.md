# Concurrency Benchmark

- Target path: `/redfish/v1/Systems/System_0`
- Total requests: 512
- Total errors: 0
- Max sustained concurrency: 128

| Clients | Requests | Errors | Throughput req/s | p50 ms | p95 ms | p99 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 128 | 0 | 1724.292 | 0.538 | 0.719 | 0.941 |
| 8 | 128 | 0 | 1826.900 | 2.964 | 3.988 | 31.039 |
| 32 | 128 | 0 | 1198.485 | 4.332 | 62.66 | 93.466 |
| 128 | 128 | 0 | 1316.171 | 31.517 | 64.161 | 65.114 |
