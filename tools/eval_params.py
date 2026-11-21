"""Play sim matches with the bot's tunable numbers overridden (SURVIVOR_PARAMS env, JSON) and print one JSON line per seed.

usage: SURVIVOR_PARAMS='{"flee_hp1": 0.4}' MECH_STREAM=... python tools/eval_params.py practice 1001,1002,1003
"""
import json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import sim  # noqa: E402

mode = sys.argv[1]
seeds = [int(s) for s in sys.argv[2].split(",") if s]
arena = os.path.join(HERE, "arena")
geo = sim.Geo(arena)
sim.HGEO = sim.Geo(arena); sim.HGEO.cache_n = 150
sim.BLK = np.load(os.path.join(HERE, "map.npz"))["blocked"]
for s in seeds:
    res, hunters = sim.run_match(mode, s, geo)
    print(json.dumps({"seed": s, "pts": [r[0] for r in res], "kills": [r[1] for r in res], "t": [r[2] for r in res],
                      "max_ms": max(r[5] for r in res)}), flush=True)
