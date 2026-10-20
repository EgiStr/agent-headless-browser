# Benchmark

Tasks follow [dev.to "16MB vs 1.2GB: benchmarking 5 AI browser automation tools"](https://dev.to/atani/16mb-vs-12gb-benchmarking-5-ai-browser-automation-tools-34pm).
The script is [`benchmarks/bench.py`](../benchmarks/bench.py).

```bash
pip install -e ".[bench]"
python benchmarks/bench.py --label mybox
python benchmarks/bench.py --label mybox --auth-report https://lookerstudio.google.com/reporting/<id>
```

Measured on 2026-10-20. "Windows" is a laptop with 12 threads and 15 GB of RAM. "Linux CLI" is a Debian container with
no X server, running in WSL2 (7.4 GB) on the same laptop. The article ran on macOS on Apple Silicon, so compare orders of
magnitude, not exact numbers.

| Metric | Windows | Linux CLI | Article: playwright-CLI / agent-browser / browser-use / Lightpanda / steel |
|---|---|---|---|
| Cold start (launch to CDP ready) | 3.38 s | 0.37 s | n/a |
| httpbin open + snapshot + close, avg of 3 | 0.58 s | 0.76 s | 1.95 / 1.88 / 4.91 / 0.92 / 0.45 s |
| 1 page, **summed RSS** (article metric) | 821 MB | 1,532 MB | 929 / 1,202 / 869 / 16 / 581 MB |
| 1 page, **USS** | 325 MB | 245 MB | not reported |
| Idle Chrome, USS | 256 MB | 203 MB | n/a |
| 3 parallel, time / USS | 1.08 s / 358 MB | 1.01 s / 273 MB | p-CLI 4.90 s / 559 MB; agent-browser 10.58 s / 4,165 MB; Lightpanda 9.34 s / ~48 MB |
| react.dev load | 2.51 s | 1.44 s | 6.22 / 4.83 / 12.77 / 4.20 / 1.45 s |
| react.dev snapshot | 15.5 KB | 15.5 KB | agent-browser 34 KB; steel 16 KB |
| github.com/microsoft/playwright snapshot | 18.3 KB | 18.3 KB | agent-browser 16–70 KB |
| Errors: DNS / 404 / timeout | `ERR_NAME_NOT_RESOLVED` / `ERR_HTTP_RESPONSE_CODE_FAILURE` / `Timeout 3000ms exceeded` | same | |
| Signed-in Looker export, 212,117 rows | 120 s | 99 s | not tested |

## Reading it

- **Summed RSS overstates Chrome about 6x on Linux.** Chrome's processes fork from a zygote and share pages, and summed
  RSS counts those pages once per process. Use USS to size machines.
- **"Parallel" here means 3 tabs in one browser that share the login.** agent-browser's 4 GB figure is 3 separate
  browsers.
- **Lightpanda wins on memory but is not Chrome.** It cannot run Looker Studio or hold a Google session.
- **Snapshot size is capped by design.** It holds 6k characters of text plus 150 refs, which keeps token use predictable.
