"""Evolutionary search over bot.PARAMS in the local sim (practice mode by default), resumable.

usage: MECH_STREAM=... python tools/tune.py [--out DIR] [--workers 18] [--gens 30] [--pop 36] [--train 36] [--recheck 6]
       python tools/tune.py --validate DIR [--top 3]      # held-out check of the best candidates vs the defaults

Each generation: every candidate plays the same `train` seeds (common seeds keep comparisons fair); the best
`recheck` play `train` more seeds and are ranked on all of them (less luck). The next generation mutates and crosses
those elites; the incumbent best and the defaults stay in as controls. Seeds 41-160 are never used here, so
--validate can score winners on matches they were not tuned on (paired against the defaults).
Writes DIR/log.jsonl (every evaluation), DIR/state.json (resume point) and DIR/best.json.
"""
import argparse, json, math, os, random, statistics as st, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("SURVIVOR_FLAGS", "")
from bot import PARAMS  # noqa: E402

# search bounds; "int" params are rounded. Everything else is searched on a log scale when positive.
BOUNDS = {
    "rng_laser_lo": (25, 140), "rng_laser_hi": (90, 260), "rng_light_lo": (4, 25), "rng_light_hi": (18, 44),
    "rng_mortar_lo": (25, 110), "rng_mortar_hi": (70, 220), "rng_gun_lo": (8, 70), "rng_gun_hi": (35, 200),
    "radial": (0.2, 1.0),
    "flee_hp1": (0.1, 0.6), "flee_ratio1": (0.6, 2.0), "flee_hp2": (0.2, 0.8), "flee_ratio2": (0.8, 2.5),
    "flee_natt": (1, 5, "int"), "flee_nlos": (1, 6, "int"), "flee_hold": (0.5, 6.0), "flee_boost_e": (0, 60),
    "strafe_min": (0.1, 1.2), "strafe_max": (0.3, 2.5), "fight_boost_e": (10, 95), "fight_boost_d": (40, 250),
    "ts_hp": (0.0, 0.15), "ts_att": (0, 150), "ts_nolos": (0, 200), "ts_sticky": (0, 120),
    "zone_lead": (5, 60), "zone_edge": (2, 30), "zone_margin": (8, 60), "zone_margin_frac": (0.1, 0.6),
    "hide_cover": (0, 150), "hide_dist": (0.01, 0.3), "hide_center": (0, 40),
    "loot_w_range": (30, 180), "loot_w_rank": (0, 40), "loot_c_range": (20, 140), "loot_c_bonus": (0, 40),
    "crate_hp": (0.3, 0.95), "crate_range": (20, 120),
    "rank_laser": (0, 4), "rank_light": (0, 4), "rank_mortar": (0, 4),
    "gun_tol": (0.8, 3.5),
}
TRAIN_BASE = 1000      # training seeds 1000+, recheck seeds 3000+; validation uses 41-160 (never trained on)
RECHECK_BASE = 3000
PY = sys.executable


def clip(k, v):
    b = BOUNDS[k]
    v = min(max(v, b[0]), b[1])
    return float(round(v)) if len(b) > 2 and b[2] == "int" else float(v)


def mutate(p, rng, sigma):
    q = dict(p)
    keys = rng.sample(sorted(BOUNDS), k=max(1, int(len(BOUNDS) * rng.uniform(0.1, 0.35))))
    for k in keys:
        lo, hi = BOUNDS[k][0], BOUNDS[k][1]
        if lo > 0 and q[k] > 0:
            q[k] = clip(k, q[k] * math.exp(rng.gauss(0, sigma)))
        else:
            q[k] = clip(k, q[k] + rng.gauss(0, sigma * 0.5) * (hi - lo))
    if q["strafe_max"] < q["strafe_min"]:
        q["strafe_max"] = q["strafe_min"]
    return q


def cross(a, b, rng):
    return {k: (a[k] if rng.random() < 0.5 else b[k]) for k in a}


def run_chunk(params, mode, seeds, timeout=1800):
    env = dict(os.environ, SURVIVOR_PARAMS=json.dumps(params), SURVIVOR_FLAGS=os.environ.get("SURVIVOR_FLAGS", ""),
               PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    r = subprocess.run([PY, os.path.join(HERE, "tools", "eval_params.py"), mode, ",".join(map(str, seeds))],
                       cwd=HERE, env=env, capture_output=True, text=True, timeout=timeout)
    out = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
    if len(out) != len(seeds):
        raise RuntimeError(f"eval failed ({len(out)}/{len(seeds)} seeds): {r.stderr[-800:]}")
    return out


def evaluate(pool, cands, mode, seeds, chunk=4):
    """cands: list of param dicts -> list of {seed: [pts...]} dicts, all seeds for every candidate."""
    jobs = []
    for ci, p in enumerate(cands):
        for k in range(0, len(seeds), chunk):
            jobs.append((ci, pool.submit(run_chunk, p, mode, seeds[k:k + chunk])))
    res = [dict() for _ in cands]
    for ci, fut in jobs:
        for row in fut.result():
            res[ci][row["seed"]] = row
    return res


def score(rows):
    return st.mean(p for r in rows.values() for p in r["pts"])


def key(p):
    return json.dumps({k: round(p[k], 4) for k in sorted(p)})


def tune(a):
    os.makedirs(a.out, exist_ok=True)
    rng = random.Random(a.seed)
    statef = os.path.join(a.out, "state.json")
    logf = open(os.path.join(a.out, "log.jsonl"), "a")
    default = {k: float(v) for k, v in PARAMS.items()}
    train = list(range(TRAIN_BASE, TRAIN_BASE + a.train))
    recheck = list(range(RECHECK_BASE, RECHECK_BASE + a.train))
    if os.path.exists(statef):
        S = json.load(open(statef))
        print(f"resuming at generation {S['gen']}", flush=True)
    else:
        S = {"gen": 0, "elites": [], "sigma": a.sigma}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        while S["gen"] < a.gens:
            g = S["gen"]; t0 = time.time()
            elites = [e["params"] for e in S["elites"]]
            cands = [default] + elites[:1]
            while len(cands) < a.pop:
                if not elites or rng.random() < 0.25:
                    base = rng.choice(elites) if elites else default
                    cands.append(mutate(base, rng, S["sigma"] * (2.0 if not elites else 1.0)))
                else:
                    pa, pb = rng.sample(elites, 2) if len(elites) > 1 else (elites[0], elites[0])
                    cands.append(mutate(cross(pa, pb, rng), rng, S["sigma"] * 0.6))
            uniq = {}
            for c in cands:
                uniq.setdefault(key(c), c)
            cands = list(uniq.values())
            res = evaluate(pool, cands, a.mode, train)
            ranked = sorted(range(len(cands)), key=lambda i: -score(res[i]))
            top = ranked[:a.recheck]
            res2 = evaluate(pool, [cands[i] for i in top], a.mode, recheck)
            scored = []
            for j, i in enumerate(top):
                rows = dict(res[i]); rows.update(res2[j])
                scored.append({"params": cands[i], "train": score(res[i]), "both": score(rows), "n": len(rows),
                               "max_ms": max(r["max_ms"] for r in rows.values()), "default": key(cands[i]) == key(default)})
            for i in range(len(cands)):
                logf.write(json.dumps({"gen": g, "params": cands[i], "train": score(res[i]), "n": len(res[i])}) + "\n")
            logf.flush()
            # elites: best by the 2x-seed score, merged with surviving old elites (re-scored ones replace their old entry)
            pool_e = {key(e["params"]): e for e in S["elites"]}
            for e in scored:
                pool_e[key(e["params"])] = e
            S["elites"] = sorted(pool_e.values(), key=lambda e: -e["both"])[: a.recheck]
            d = next((e for e in scored if e["default"]), None)
            best = S["elites"][0]
            S["sigma"] = max(0.08, S["sigma"] * 0.93)
            S["gen"] = g + 1
            json.dump(S, open(statef, "w"), indent=1)
            json.dump(best, open(os.path.join(a.out, "best.json"), "w"), indent=1)
            print(f"gen {g:2d}: {len(cands)} cands, best train {score(res[ranked[0]]):.2f} | elite #1 {best['both']:.2f} on {best['n']} seeds"
                  f"{' (default ' + format(d['both'], '.2f') + ')' if d else ''} | default train {score(res[0]):.2f} | "
                  f"sigma {S['sigma']:.2f} | {time.time() - t0:.0f}s", flush=True)


def validate(a):
    S = json.load(open(os.path.join(a.validate, "state.json")))
    default = {k: float(v) for k, v in PARAMS.items()}
    cands = [default] + [e["params"] for e in S["elites"][: a.top]]
    seeds = list(range(41, 161))
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        res = evaluate(pool, cands, "practice", seeds)
        resf = evaluate(pool, cands, "ffa", list(range(1, 16)), chunk=1)
    base = res[0]; basef = resf[0]
    out = []
    for i, c in enumerate(cands):
        d = [st.mean(res[i][s]["pts"]) - st.mean(base[s]["pts"]) for s in seeds]
        df = [st.mean(resf[i][s]["pts"]) - st.mean(basef[s]["pts"]) for s in range(1, 16)]
        se = st.stdev(d) / math.sqrt(len(d)) if i else 0; sef = st.stdev(df) / math.sqrt(len(df)) if i else 0
        row = {"cand": "default" if i == 0 else f"elite{i}", "practice": score(res[i]), "practice_delta": st.mean(d), "practice_se": se,
               "ffa": score(resf[i]), "ffa_delta": st.mean(df), "ffa_se": sef,
               "kills": st.mean(k for r in res[i].values() for k in r["kills"]),
               "max_ms": max(r["max_ms"] for r in list(res[i].values()) + list(resf[i].values())), "params": c}
        out.append(row)
        print(f"{row['cand']:8s} practice {row['practice']:.2f} ({row['practice_delta']:+.2f} ± {1.96 * se:.2f}) | "
              f"ffa {row['ffa']:.2f} ({row['ffa_delta']:+.2f} ± {1.96 * sef:.2f}) | kills {row['kills']:.2f} | max {row['max_ms']:.1f} ms", flush=True)
    json.dump(out, open(os.path.join(a.validate, "validation.json"), "w"), indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "tune"))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--gens", type=int, default=30)
    ap.add_argument("--pop", type=int, default=36)
    ap.add_argument("--train", type=int, default=36)
    ap.add_argument("--recheck", type=int, default=6)
    ap.add_argument("--sigma", type=float, default=0.35)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mode", default="practice")
    ap.add_argument("--validate")
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args()
    validate(a) if a.validate else tune(a)
