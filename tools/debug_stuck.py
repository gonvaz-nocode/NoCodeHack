"""Diagnose a stuck bot: rerun one sim match and log our bot's state between two times.

usage: MECH_STREAM=... python tools/debug_stuck.py <practice|ffa> <seed> <t0> <t1>
"""
import math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import sim  # noqa: E402
import bot as botmod  # noqa: E402

mode, seed, t0, t1 = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
orig_act = botmod.Bot.act
SIM = {}
orig_init = sim.Sim.__init__


def init(self, *a, **k):
    orig_init(self, *a, **k)
    SIM["s"] = self


sim.Sim.__init__ = init


def act(self, obs):
    a = orig_act(self, obs)
    t = obs["t"]
    if t0 <= t <= t1 and round(t * 10) % 5 == 0:
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        g = self.geo
        hide = self.hide
        wl = g.walk_line(x, y, hide[0], hide[1]) if hide else None
        f = g.field(hide[0], hide[1], x, y) if hide else None
        st = g.steer(f, x, y) if f is not None else None
        s = SIM["s"]; m = s.mechs[me["id"]]
        blk = s.blocked_at(x + a["move"][0] * 0.6, y + a["move"][1] * 0.6)
        nj, ni = g.nav.cell(x, y) if hasattr(g, "nav") else (None, None)
        print(f"t={t:6.1f} pos=({x:7.2f},{y:7.2f}) vel=({me['vel'][0]:5.2f},{me['vel'][1]:5.2f}) e={me['energy']:5.1f} "
              f"move={a['move']} boost={a['boost']} jump={a.get('jump')} stuck={self.stuck_ticks} unstick_until={self.unstick_until:.1f} "
              f"hide=({hide[0]:.1f},{hide[1]:.1f}) walk_line={wl} field={'none' if f is None else 'ok'} steer={None if st is None else (tuple(round(v, 2) for v in st[0]) if st[0] else None, round(st[1], 1))} "
              f"sim_blocked_ahead={blk} free_here={bool(g.free[g.cell(x, y)]) if hasattr(g, 'free') else '?'}", flush=True)
    return a


botmod.Bot.act = act
geo = sim.Geo(os.path.join(HERE, "arena"))
sim.HGEO = sim.Geo(os.path.join(HERE, "arena")); sim.HGEO.cache_n = 150
sim.BLK = np.load(os.path.join(HERE, "map.npz"))["blocked"]
res, _ = sim.run_match(mode, seed, geo)
print("result", res)
