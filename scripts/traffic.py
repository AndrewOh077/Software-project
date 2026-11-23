"""
간단한 트래픽 생성기 (Windows/Mac/Linux 모두 됨, 설치할 것 없음).
세 서버에 번갈아 요청을 보내고, 1초마다 성공/실패 개수를 보여줌.

  python scripts/traffic.py              # 초당 20개, 60초
  python scripts/traffic.py --rps 50 --seconds 120
"""

import argparse
import threading
import time
import urllib.error
import urllib.request
from collections import Counter

PORTS = [8001, 8002, 8003]


def hit(port: int, counts: Counter, lock: threading.Lock) -> None:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/work?ms=50", timeout=5) as r:
            key = r.status
    except urllib.error.HTTPError as e:
        key = e.code
    except Exception:
        key = "no answer"
    with lock:
        counts[(port, key)] += 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rps", type=int, default=20)
    p.add_argument("--seconds", type=int, default=60)
    a = p.parse_args()

    lock = threading.Lock()
    i = 0
    print(f"Sending ~{a.rps} req/s for {a.seconds}s (Ctrl+C to stop)")
    try:
        for sec in range(a.seconds):
            counts: Counter = Counter()
            threads = []
            for _ in range(a.rps):
                t = threading.Thread(target=hit, args=(PORTS[i % 3], counts, lock), daemon=True)
                t.start()
                threads.append(t)
                i += 1
                time.sleep(1 / a.rps)
            for t in threads:
                t.join(timeout=5)
            line = []
            for n, port in enumerate(PORTS, 1):
                ok = counts[(port, 200)]
                bad = sum(v for (pt, k), v in counts.items() if pt == port and k != 200)
                line.append(f"app{n}: {ok} ok" + (f" / {bad} FAIL" if bad else ""))
            print(f"[{sec + 1:>3}s] " + " | ".join(line))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
