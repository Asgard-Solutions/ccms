"""Standalone repro: 40 rapid bad logins → expect 429s + rate_limit.block log line + counter bump."""
import os, re, time, requests, pathlib

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"

SUP_OUT = pathlib.Path("/var/log/supervisor/backend.out.log")
SUP_ERR = pathlib.Path("/var/log/supervisor/backend.err.log")

def counter(text, name, labels):
    pat = re.compile(rf'^{re.escape(name)}\{{([^}}]*)\}}\s+([0-9.eE+-]+)', re.M)
    want = set(f'{k}="{v}"' for k, v in labels.items())
    best = 0.0
    for m in pat.finditer(text):
        parts = set(p.strip() for p in m.group(1).split(","))
        if want.issubset(parts):
            best = float(m.group(2))
    return best

def fetch_metrics():
    return requests.get(f"{API}/metrics", timeout=10).text

def tail(p, n=500_000):
    if not p.exists():
        return ""
    size = p.stat().st_size
    with p.open("rb") as f:
        if size > n:
            f.seek(size - n)
        return f.read().decode("utf-8", errors="ignore")

# Establish baseline
err_before_size = SUP_ERR.stat().st_size if SUP_ERR.exists() else 0

m0 = fetch_metrics()
before = counter(m0, "ccms_rate_limit_blocks_total", {"source": "local"})

s = requests.Session()
status_counts = {}
for i in range(40):
    r = s.post(f"{API}/auth/login",
               json={"email": "noone_retest@ccms.app", "password": "bad"},
               timeout=5)
    status_counts[r.status_code] = status_counts.get(r.status_code, 0) + 1

time.sleep(1.5)

m1 = fetch_metrics()
after = counter(m1, "ccms_rate_limit_blocks_total", {"source": "local"})

# Check logs for rate_limit.block WARNING line
out_tail = tail(SUP_OUT)
err_tail = tail(SUP_ERR)

rb_pat = re.compile(r'"event"\s*:\s*"rate_limit\.block"')
rb_out_hits = len(rb_pat.findall(out_tail))
rb_err_hits = len(rb_pat.findall(err_tail))

# New tracebacks in err.log since start
new_err = ""
if SUP_ERR.exists():
    with SUP_ERR.open("rb") as f:
        f.seek(err_before_size)
        new_err = f.read().decode("utf-8", errors="ignore")
tb_count = new_err.count("Traceback (most recent call last):")
sec_logger_tb = ("security_logger" in new_err) or ("rate_limit" in new_err and "TypeError" in new_err)

print("=== RETEST RESULTS ===")
print(f"status_counts: {status_counts}")
print(f"counter ccms_rate_limit_blocks_total{{source=local}} before={before} after={after}")
print(f"rate_limit.block hits: out={rb_out_hits} err={rb_err_hits}")
print(f"New traceback count in backend.err.log since test start: {tb_count}")
print(f"security_logger/rate_limit TypeError in new err tail: {sec_logger_tb}")

# Sample a rate_limit.block line if any
for line in (out_tail + err_tail).splitlines():
    if '"event":"rate_limit.block"' in line or '"event": "rate_limit.block"' in line:
        print(f"SAMPLE LINE: {line.strip()[:400]}")
        break

# Assertions summary
ok_429 = status_counts.get(429, 0) >= 1 and 500 not in status_counts
ok_counter = after >= before + 1
ok_log = (rb_out_hits + rb_err_hits) >= 1
ok_no_tb = not sec_logger_tb

print("---")
print(f"PASS 429 (no 500): {ok_429}")
print(f"PASS counter bumped: {ok_counter}")
print(f"PASS rate_limit.block log line: {ok_log}")
print(f"PASS no security_logger/rate_limit tracebacks: {ok_no_tb}")
print(f"OVERALL: {'PASS' if all([ok_429, ok_counter, ok_log, ok_no_tb]) else 'FAIL'}")
