#!/usr/bin/env python3
"""旋钮配置台 —— 本地小服务。

    python3 scripts/knobs_server.py        # 打开 http://127.0.0.1:8777

## 为什么是本地服务，不是一个托管页面

写稿/采集/审核跑在本机 launchd 上，读的是本地 `config/knobs.json`。
托管在云端的页面改了值，没有任何东西会把它拉下来 —— 那就多出一个「等人同步」的
环节，而这类环节的实际执行率是零。本地页面直接写那个 JSON，改完下一轮定时任务
就按新值跑，中间没有人。

## 只监听 127.0.0.1，不加认证

它能改的是本机一个 JSON 文件，而能连上 127.0.0.1 的人本来就能直接编辑那个文件。
加一层认证不增加任何安全性，只增加一个会忘的密码。
⛔ 但也因此**不要把 HOST 改成 0.0.0.0** —— 那一改性质就变了。
"""
import csv
import json
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from knobs import CHANGELOG, CONFIG, REPO, SCHEMA, K, all_values  # noqa: E402

HOST, PORT = "127.0.0.1", 8777
PAGE = REPO / "config" / "knobs.html"
LOG_COLS = ["时间", "旋钮", "旧值", "新值", "备注"]


def forecast() -> dict:
    """算「后果预估」要用的真实底数。页面靠它把旋钮换算成看得懂的后果。

    ⛔ 这些数必须是**真实读出来的**，不能在前端写死。写死的预估比没有预估更坏：
    它看起来像证据，实际是装饰。
    """
    import re
    from collections import Counter
    from datetime import date, timedelta
    sucai = REPO / "xhs" / "素材库"
    out = {}

    # 采集速率（供给）
    win = K("MATERIAL_WINDOW_DAYS")
    cutoff = (date.today() - timedelta(days=win)).isoformat()
    inflow, days = 0, set()
    p = sucai / "运行日志.csv"
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            d = (r.get("日期") or "")[:10]
            if d >= cutoff:
                days.add(d)
                try:
                    inflow += int((r.get("本轮新增条数") or "0").strip() or 0)
                except ValueError:
                    pass
    out["inflow"] = inflow
    out["window_days"] = win
    out["inflow_per_day"] = round(inflow / max(win, 1), 1)

    # 已写稿（消耗）
    drafts = list(sucai.glob("成稿_*.md")) + list((sucai / "归档稿").glob("成稿_*.md"))
    out["written_in_window"] = sum(
        1 for f in drafts
        if (m := re.match(r"成稿_(\d{4}-\d{2}-\d{2})_", f.name)) and m.group(1) >= cutoff)

    # 审核分分布（给 PASS_SCORE 用）
    scores = []
    p = sucai / "审核记录.csv"
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            if (r.get("审核方") or "").strip() != "独立审核":
                continue
            try:
                scores.append(int(str(r.get("总分") or "").strip()))
            except ValueError:
                pass
    out["scores"] = sorted(scores)

    # 候选词的案例支撑分布（给 MATERIAL_MIN_CASES 用）
    try:
        sys.path.insert(0, str(REPO / "scripts" / "xhs-loop"))
        import refine_loop as RL
        pool = [r for r in csv.DictReader((sucai / "词库.csv").open(encoding="utf-8-sig"))
                if (r.get("关键词") or "").strip()
                and (r.get("状态") or "").strip() in ("已验证", "候选")]
        used = RL.used_keywords()
        cnt = Counter()
        for r in pool:
            kw = r["关键词"].strip()
            if kw not in used:
                cnt[min(RL.material_backing(kw), 20)] += 1
        out["case_hist"] = {str(k): v for k, v in sorted(cnt.items())}
    except Exception as e:                                   # noqa: BLE001
        out["case_hist"] = {}
        out["case_hist_error"] = str(e)[:120]
    return out


def save(values: dict, note: str) -> list:
    """写盘 + 记 changelog。返回实际发生的改动。"""
    before = all_values()
    keep = {}
    for k, s in SCHEMA.items():
        v = values.get(k, before[k])
        caster = int if s["type"] == "int" else float
        try:
            v = caster(v)
        except (TypeError, ValueError):
            v = s["default"]
        v = min(max(v, s["min"]), s["max"])          # 服务端夹紧，不信前端
        if v != s["default"]:
            keep[k] = v
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".json.tmp")            # 原子替换：别留下半个文件
    tmp.write_text(json.dumps(keep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG)

    after = all_values()
    diffs = [(k, before[k], after[k]) for k in SCHEMA if before[k] != after[k]]
    if diffs:
        # ⛔ 改了什么必须留痕。这个仓库反复吃过「某个数被改过、没人知道为什么」的亏
        # （VIEWS_BASE 拖了一个月没调、评分卡权重改了三次没人对得上）。
        new = not CHANGELOG.exists()
        with CHANGELOG.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOG_COLS)
            if new:
                w.writeheader()
            ts = datetime.now().isoformat(timespec="seconds")
            for k, o, n in diffs:
                w.writerow({"时间": ts, "旋钮": k, "旧值": o, "新值": n, "备注": note})
    return diffs


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if not PAGE.exists():
                return self._send(500, "找不到 config/knobs.html", "text/plain; charset=utf-8")
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/knobs":
            return self._send(200, json.dumps(
                {"schema": SCHEMA, "values": all_values(), "forecast": forecast()},
                ensure_ascii=False))
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_PUT(self):
        if self.path != "/api/knobs":
            return self._send(404, "not found", "text/plain; charset=utf-8")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, json.dumps({"error": "bad json"}))
        diffs = save(payload.get("values") or {}, (payload.get("note") or "").strip())
        for k, o, v in diffs:
            print(f"  · {k}: {o} → {v}")
        return self._send(200, json.dumps(
            {"ok": True, "changed": [{"key": k, "from": o, "to": v} for k, o, v in diffs],
             "values": all_values(), "forecast": forecast()}, ensure_ascii=False))

    def log_message(self, *a):
        pass                                          # 别把每个请求刷进终端


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    print(f"旋钮配置台 → {url}    （Ctrl-C 停）")
    print(f"配置文件：{CONFIG}")
    try:
        webbrowser.open(url)
    except Exception:                                 # noqa: BLE001
        pass
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
