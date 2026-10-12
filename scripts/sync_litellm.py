"""LiteLLM の単価表 (model_prices_and_context_window.json) から pricing.json を更新する。

使い方:
  python scripts/sync_litellm.py litellm.json pricing.json --summary summary.md

- pricing.json にあるモデル: LiteLLM の値と違えば LiteLLM の値にする。LiteLLM に無い項目・モデルは今の値のまま
- LiteLLM にあって pricing.json に無い Claude / GPT のモデル: 追加する (sync-ignore.txt のパターンに当たるものは除く)
- 値を変えたら version を上げて pricing.json を書き直す
- 変わったかどうかを GitHub Actions の出力 (changed=true/false) に書き、PR の本文にする要約を --summary に書く
"""
import argparse
import datetime
import json
import os
import re
import sys
from decimal import Decimal
from zoneinfo import ZoneInfo

FIELDS = ["input", "output", "cacheWrite5m", "cacheWrite1h", "cacheRead"]
# pricing.json の項目 → LiteLLM の項目 (1 トークンあたりの USD)
BASE_KEYS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cacheWrite5m": "cache_creation_input_token_cost",
    "cacheWrite1h": "cache_creation_input_token_cost_above_1hr",
    "cacheRead": "cache_read_input_token_cost",
}
LC_SUFFIXES = ["100k", "128k", "200k", "272k", "512k"]
PROVIDERS = {"anthropic", "openai"}
CANDIDATE = re.compile(r"^(claude-[a-z]+-\d|gpt-\d)")
# 日付つき・Bedrock 形式・latest などの別名は追加しない (アプリは日付の接尾辞を外して照合する)
ALIAS = re.compile(r"-\d{8}|-\d{4}-\d{2}-\d{2}|-\d{4}$|v\d:\d|latest")


def to_ours(litellm_id):
    """gpt-5.4-pro → gpt-5-4-pro (アプリは . を - に読み替えて照合する)"""
    return re.sub(r"^gpt-(\d+)\.(\d+)", r"gpt-\1-\2", litellm_id)


def plain(d):
    """10 を 1E+1 と書かないよう、指数表記を使わない Decimal にする"""
    d = d.normalize()
    return Decimal(format(d, "f"))


def per_million(v):
    """1 トークンあたり → 100 万トークンあたり (浮動小数の誤差を丸める)"""
    return plain(Decimal(str(round(v * 1_000_000, 6))))


def num(v):
    return plain(Decimal(str(v)))


def map_prices(src, cur, suffix=None):
    """LiteLLM の 1 エントリ (または長文脈の割増) を pricing.json の形にする。LiteLLM に無い項目は cur の値を使う"""
    keys = {k: (v if suffix is None else f"{v}_above_{suffix}_tokens") for k, v in BASE_KEYS.items()}
    if suffix is not None:
        keys["cacheWrite1h"] = f"cache_creation_input_token_cost_above_1hr_above_{suffix}_tokens"
    out = {}
    for f in FIELDS:
        v = src.get(keys[f])
        out[f] = per_million(v) if isinstance(v, (int, float)) else None
    # キャッシュ書込 1h が無い (OpenAI など、書込の時間で単価が変わらない): 今の値が 5m と同じなら 5m に合わせる
    if out["cacheWrite1h"] is None and out["cacheWrite5m"] is not None:
        if cur is None or num(cur.get("cacheWrite1h", 0)) == num(cur.get("cacheWrite5m", 0)):
            out["cacheWrite1h"] = out["cacheWrite5m"]
    for f in FIELDS:
        if out[f] is None:
            if cur is not None:
                out[f] = num(cur.get(f, 0))
            elif f == "cacheRead":
                out[f] = out["input"]   # キャッシュ読込の割引が無いモデル (Pro 系) は入力と同じ
            else:
                out[f] = Decimal(0)
    return out


def lc_suffix_of(threshold):
    s = f"{threshold // 1000}k"
    return s if threshold % 1000 == 0 and s in LC_SUFFIXES else None


def detect_lc(src):
    for s in LC_SUFFIXES:
        if isinstance(src.get(f"input_cost_per_token_above_{s}_tokens"), (int, float)):
            return s
    return None


def load_ignore(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f]
    return [re.compile(ln) for ln in lines if ln and not ln.startswith("#")]


def next_version(old):
    today = int(datetime.datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d"))
    return old + 1 if old // 100 == today else today * 100 + 1


def fmt_model(mid, p, width):
    def body(d):
        return ", ".join(f'"{f}": {d[f]}' for f in FIELDS)
    line = f'    {json.dumps(mid) + ":":<{width + 3}} {{ {body(p)}'
    if "longContext" in p:
        lc = p["longContext"]
        line += f',\n{" " * (width + 8)}"longContext": {{ "threshold": {lc["threshold"]}, {body(lc)} }}'
    return line + " }"


def dump(t):
    """1 モデル 1 行 (長文脈は次の行) の、差分を読みやすい形で書く"""
    width = max(len(json.dumps(k)) for k in t["models"])
    head = [f'  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}' for k, v in t.items() if k != "models"]
    models = ",\n".join(fmt_model(k, v, width) for k, v in t["models"].items())
    return "{\n" + ",\n".join(head) + ',\n  "models": {\n' + models + "\n  }\n}\n"


def same(a, b):
    return all(num(a.get(f, 0)) == num(b.get(f, 0)) for f in FIELDS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("litellm")
    ap.add_argument("pricing")
    ap.add_argument("--summary")
    ap.add_argument("--ignore", default="sync-ignore.txt")
    ap.add_argument("--reformat", action="store_true", help="値が変わらなくても整形して書き直す")
    a = ap.parse_args()

    with open(a.litellm, encoding="utf-8") as f:
        src = json.load(f)
    with open(a.pricing, encoding="utf-8-sig") as f:
        t = json.load(f)
    ignore = load_ignore(a.ignore)
    models = t["models"]
    by_ours = {}
    for k, v in src.items():
        if isinstance(v, dict) and v.get("litellm_provider") in PROVIDERS:
            by_ours.setdefault(to_ours(k), v)

    changed, added, missing, notes = [], [], [], []
    for mid, cur in list(models.items()):
        if mid == "<synthetic>":
            continue
        s = by_ours.get(mid)
        if s is None:
            missing.append(mid)
            continue
        new = map_prices(s, cur)
        diffs = [f"{f} {cur.get(f, 0)} → {new[f]}" for f in FIELDS if num(cur.get(f, 0)) != new[f]]
        lc = cur.get("longContext")
        if lc:
            suf = lc_suffix_of(lc["threshold"])
            if suf and detect_lc(s) == suf:
                nlc = map_prices(s, lc, suf)
                diffs += [f"長文脈 {f} {lc.get(f, 0)} → {nlc[f]}" for f in FIELDS if num(lc.get(f, 0)) != nlc[f]]
                new["longContext"] = {"threshold": lc["threshold"], **nlc}
            else:
                new["longContext"] = lc
                notes.append(f"`{mid}`: 長文脈の割増 (> {lc['threshold']:,}) が LiteLLM で見つからないので、今の値のまま")
        elif detect_lc(s):
            notes.append(f"`{mid}`: LiteLLM には長文脈の割増 ({detect_lc(s)} 超) があるが、pricing.json に無い。必要なら手で足す")
        if diffs:
            changed.append((mid, diffs))
        models[mid] = {**{f: new[f] for f in FIELDS}, **({"longContext": new["longContext"]} if "longContext" in new else {})}

    for mid, s in sorted(by_ours.items()):
        if mid in models or not CANDIDATE.match(mid) or ALIAS.search(mid):
            continue
        if s.get("mode") not in ("chat", "responses") or not isinstance(s.get("input_cost_per_token"), (int, float)):
            continue
        if any(p.search(mid) for p in ignore):
            continue
        p = map_prices(s, None)
        suf = detect_lc(s)
        if suf:
            p["longContext"] = {"threshold": int(suf[:-1]) * 1000, **map_prices(s, None, suf)}
        models[mid] = p
        added.append((mid, p))

    if "<synthetic>" in models:   # 末尾に置く
        models["<synthetic>"] = models.pop("<synthetic>")

    has_change = bool(changed or added)
    if has_change:
        old = t["version"]
        t["version"] = next_version(old)
    if has_change or a.reformat:
        with open(a.pricing, "w", encoding="utf-8", newline="\n") as f:
            f.write(dump(t))

    lines = []
    if has_change:
        lines.append(f"LiteLLM の単価表との差分を取り込み、version を {t['version']} にした。\n")
    if changed:
        lines.append("## 単価が変わったモデル\n")
        lines += [f"- `{m}`: " + ", ".join(d) for m, d in changed]
        lines.append("")
    if added:
        lines.append("## 追加したモデル\n")
        lines.append("要らないものは、この PR から行を消し、`sync-ignore.txt` にパターンを足す (次回から追加しない)。\n")
        lines += [f"- `{m}`: 入力 {p['input']} / 出力 {p['output']}" + (" (長文脈の割増あり)" if "longContext" in p else "") for m, p in added]
        lines.append("")
    if notes:
        lines.append("## 確認してほしいこと\n")
        lines += [f"- {n}" for n in notes]
        lines.append("")
    if missing:
        lines.append("<details><summary>LiteLLM に無いモデル (今の値のまま)</summary>\n")
        lines += [f"- `{m}`" for m in missing]
        lines.append("\n</details>\n")
    summary = "\n".join(lines)
    print(summary or "変更なし")
    if a.summary:
        with open(a.summary, "w", encoding="utf-8") as f:
            f.write(summary)
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if has_change else 'false'}\nversion={t['version']}\n")


if __name__ == "__main__":
    sys.exit(main())
