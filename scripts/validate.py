"""pricing.json を確かめる (アプリ側の PricingUpdate.Validate / CompareWith と同じ基準)。

使い方:
  python scripts/validate.py pricing.json                 形式・値の範囲だけ確かめる
  python scripts/validate.py pricing.json base.json       前の版と比べて、版が上がっているか・単価が極端に変わっていないかも確かめる
"""
import datetime
import json
import re
import sys

SUPPORTED_SCHEMA = 1
MIN_MODELS = 5
MAX_PRICE = 1000
MAX_RATIO = 10
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-:/]*$|^<synthetic>$")
FIELDS = ["input", "output", "cacheWrite5m", "cacheWrite1h", "cacheRead"]


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def prices(p):
    return [p.get(k, 0) for k in FIELDS]


def validate(t):
    errors = []
    if t.get("schema") != SUPPORTED_SCHEMA:
        errors.append(f"schema は {SUPPORTED_SCHEMA} にしてください (今: {t.get('schema')})")
    v = t.get("version")
    if not isinstance(v, int) or not (2020010100 <= v < 2100010100):
        errors.append(f"version は 日付 8 桁 + 連番 2 桁 の整数にしてください (今: {v})")
    else:
        try:
            d = datetime.datetime.strptime(str(v // 100), "%Y%m%d").date()
        except ValueError:
            d = None
        if d is None:
            errors.append(f"version の日付が実在しません: {v}")
        elif d > datetime.date.today() + datetime.timedelta(days=3):
            # 先の日付の版を取り込んだアプリは、それより小さい正しい版を二度と取り込めなくなる
            errors.append(f"version の日付が先すぎます: {v}")
    models = t.get("models") or {}
    if len(models) < MIN_MODELS:
        errors.append(f"models が少なすぎます ({len(models)} 件)")
    for mid, p in models.items():
        if len(mid) > 100 or not ID.match(mid):
            errors.append(f"モデルID が不正です: {mid}")
        for k in FIELDS:
            x = p.get(k, 0)
            if not isinstance(x, (int, float)) or not (0 <= x <= MAX_PRICE):
                errors.append(f"{mid}.{k} が範囲外です: {x}")
        lc = p.get("longContext")
        if lc is not None:
            th = lc.get("threshold", 0)
            if not isinstance(th, int) or not (0 < th <= 100_000_000):
                errors.append(f"{mid}.longContext.threshold が不正です: {th}")
            for k in FIELDS:
                x = lc.get(k, 0)
                if not isinstance(x, (int, float)) or not (0 <= x <= MAX_PRICE):
                    errors.append(f"{mid}.longContext.{k} が範囲外です: {x}")
    return errors


def find(models, mid):
    """アプリと同じく最長前方一致で探す"""
    if mid in models:
        return models[mid]
    hits = [k for k in models if mid.lower().startswith(k.lower())]
    return models[max(hits, key=len)] if hits else None


def compare(new, base):
    errors = []
    if new.get("version", 0) <= base.get("version", 0):
        errors.append(f"version を上げてください ({base.get('version')} → {new.get('version')})。上げないとアプリは取り込みません")
    old_models = base.get("models") or {}
    for mid, p in (new.get("models") or {}).items():
        cur = find(old_models, mid)
        if cur is None:
            continue
        if (cur.get("input", 0) > 0 and p.get("input", 0) == 0) or (cur.get("output", 0) > 0 and p.get("output", 0) == 0):
            errors.append(f"有料だった単価が 0 になっています: {mid}")
        if mid not in old_models:
            continue  # 新しいモデルID は倍率を比べない (上位版・下位版は親の 10 倍以上違うのが普通)
        for k, a, b in zip(FIELDS, prices(cur), prices(p)):
            if a > 0 and b > 0 and (b / a > MAX_RATIO or a / b > MAX_RATIO):
                errors.append(f"{mid}.{k} が {MAX_RATIO} 倍を超えて変わっています ({a} → {b})。アプリはこの版を取り込みません")
    removed = sorted(set(old_models) - set(new.get("models") or {}))
    if removed:
        print("注意: 消したモデルは、すでに取り込んだ利用者の表からは消えません: " + ", ".join(removed))
    return errors


def main():
    new = load(sys.argv[1])
    errors = validate(new)
    if len(sys.argv) > 2:
        errors += compare(new, load(sys.argv[2]))
    for e in errors:
        print("NG: " + e)
    if errors:
        sys.exit(1)
    print(f"OK: version {new['version']} / {len(new['models'])} モデル")


if __name__ == "__main__":
    main()
