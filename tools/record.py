"""Record tools/sim.py matches as replay JSON for viewer/index.html.

usage: MECH_STREAM=<kit>/check/stream.jsonl.gz python tools/record.py [practice|ffa] [seed] [out.json]

Wraps the simulator without changing it: every 0.2 s it samples each mech, and it logs
projectiles, beams, explosions, hits, deaths, loot and the bot's debug label as events.
The simulator is a stand-in for the real server (see CLAUDE.md), so treat replays as a
picture of what the bot tries to do, not of real match results.
"""
import json, math, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import sim  # noqa: E402  (loads CFG from MECH_STREAM)
import mechfight as mf  # noqa: E402

SAMPLE_TICKS = 4  # 20 Hz sim -> 5 Hz frames; the viewer interpolates
WCODE = {None: 0, "gun": 1, "mortar": 2, "laser": 3, "lightning": 4}


def r1(v):
    return round(float(v), 1)


class Recorder:
    def __init__(self):
        self.sim = None
        self.frames = []
        self.ev = {"proj": [], "beam": [], "boom": [], "hit": [], "death": [], "item": [], "label": []}
        self.live_proj = {}
        self.items = {}
        self.labels = {}

    # ---- hooks
    def install(self):
        rec = self
        S = sim.Sim
        orig_init, orig_step, orig_damage = S.__init__, S.step, S.damage
        orig_hitscan, orig_explode = S.hitscan, S.explode

        def init(self_, geo, seed, bots):
            for k, b in enumerate(bots):  # give each of our bots its own debug buffer (the sim shares mf.draw)
                if not isinstance(b, sim.Hunter):
                    b._rec_id = k
                    b.act = rec.wrap_act(b)
            orig_init(self_, geo, seed, bots)
            rec.sim = self_
            rec.sample()

        def step(self_):
            orig_step(self_)
            rec.diff_entities()
            if self_.tick % SAMPLE_TICKS == 0:
                rec.sample()

        def damage(self_, tgt, amount, weapon, by, src_xy):
            if tgt.alive:
                rec.ev["hit"].append([round(self_.t, 2), -1 if by is None else by, tgt.id, weapon, round(amount)])
                was = tgt.alive
                orig_damage(self_, tgt, amount, weapon, by, src_xy)
                if was and not tgt.alive:
                    rec.ev["death"].append([round(self_.t, 2), tgt.id, -1 if by is None else by, weapon, self_.alive_count])
            else:
                orig_damage(self_, tgt, amount, weapon, by, src_xy)

        def hitscan(self_, m, d, rng_, dmg, kind):
            n0 = len(rec.ev["hit"])
            orig_hitscan(self_, m, d, rng_, dmg, kind)
            o = (m.pos[0], m.pos[1], m.pos[2] + 7)
            hits = [h for h in rec.ev["hit"][n0:] if h[0] == round(self_.t, 2) and h[1] == m.id]
            if hits:
                t = self_.mechs[hits[0][2]].pos
                end = (t[0], t[1], t[2] + 4.5)
            else:  # march until a wall blocks the beam
                end = o
                for s in range(4, int(rng_), 4):
                    p = (o[0] + d[0] * s, o[1] + d[1] * s, o[2] + d[2] * s)
                    if not self_.g.los(o, p):
                        break
                    end = p
            rec.ev["beam"].append([round(self_.t, 2), m.id, kind, r1(o[0]), r1(o[1]), r1(o[2]), r1(end[0]), r1(end[1]), r1(end[2]), 1 if hits else 0])

        def explode(self_, p, at, direct=None):
            rec.ev["boom"].append([round(self_.t, 2), r1(at[0]), r1(at[1]), r1(at[2]), p["by"]])
            orig_explode(self_, p, at, direct)

        S.__init__, S.step, S.damage, S.hitscan, S.explode = init, step, damage, hitscan, explode

    def wrap_act(self, b):
        orig = b.act
        rec = self

        def act(o):
            mf.draw.clear()
            a = orig(o)
            d = mf.draw.take() or {}
            lab = d.get("label")
            sid = b._rec_id
            if lab is not None and sid is not None and rec.labels.get(sid) != lab:
                rec.labels[sid] = lab
                rec.ev["label"].append([round(o["t"], 2), sid, lab])
            return a
        return act

    # ---- sampling
    def diff_entities(self):
        s = self.sim
        t = round(s.t, 2)
        cur = {p["id"]: p for p in s.projectiles}
        for pid, p in cur.items():
            if pid not in self.live_proj:
                self.live_proj[pid] = len(self.ev["proj"])
                self.ev["proj"].append([t, p["by"], p["kind"], *map(r1, p["pos"]), *map(r1, p["vel"]), None, None, None, None])
        for pid in list(self.live_proj):
            if pid not in cur:
                e = self.ev["proj"][self.live_proj.pop(pid)]
                e[9] = t
        items = {}
        for k, p in s.pickups.items():
            items[("w", k)] = (p["kind"], p["pos"])
        for k, c in s.crates.items():
            items[("c", k)] = ("crate", c["pos"])
        for k, c in s.cubes.items():
            items[("q", k)] = ("cube", c["pos"])
        for key, (kind, pos) in items.items():
            if key not in self.items:
                self.items[key] = len(self.ev["item"])
                self.ev["item"].append([t, None, kind, r1(pos[0]), r1(pos[1]), r1(pos[2])])
        for key in list(self.items):
            if key not in items:
                self.ev["item"][self.items.pop(key)][1] = t

    def sample(self):
        s = self.sim
        c, r, z = s.zone_now()
        ms = []
        for m in s.mechs:
            ms.append([r1(m.pos[0]), r1(m.pos[1]), r1(m.pos[2]), round(m.aim[0], 2), max(0, round(m.hp)), round(m.max_hp),
                       1 if m.alive else 0, m.cubes, WCODE[m.weapons[0]["kind"] if m.weapons[0] else None],
                       WCODE[m.weapons[1]["kind"] if m.weapons[1] else None], round(getattr(m, "energy", 0))])
        self.frames.append({"t": round(s.t, 2), "z": [r1(c[0]), r1(c[1]), r1(r), r1(z["nc"][0]), r1(z["nc"][1]), r1(z["nr"])], "m": ms})


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "practice"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "..", "viewer", "replays", f"{mode}-{seed}.json")
    rec = Recorder()
    rec.install()
    geo = sim.Geo(os.path.join(HERE, "arena"))
    sim.HGEO = sim.Geo(os.path.join(HERE, "arena")); sim.HGEO.cache_n = 150
    sim.BLK = np.load(os.path.join(HERE, "map.npz"))["blocked"]
    t0 = time.time()
    res, hunter_pts = sim.run_match(mode, seed, geo)
    s = rec.sim
    for e in rec.ev["proj"]:
        if e[9] is None:
            e[9] = round(s.t, 2)
    for e in rec.ev["item"]:
        pass
    ours = [k for k, m in enumerate(s.mechs) if not isinstance(m.bot, sim.Hunter)]
    mechs = []
    for k, m in enumerate(s.mechs):
        h = isinstance(m.bot, sim.Hunter)
        mechs.append({"id": k, "ours": not h, "name": (f"hunter-{k} (skill {m.bot.skill})" if h else f"{getattr(m.bot, 'name', 'bot')}-{k}"),
                      "skill": m.bot.skill if h else None, "place": m.place, "kills": m.kills})
    replay = {"format": "mech-replay/1", "source": "tools/sim.py (local stand-in, not the real server)", "mode": mode, "seed": seed,
              "variant": os.environ.get("SURVIVOR_FLAGS", "") or "none",
              "duration": round(s.t, 2), "dt": SAMPLE_TICKS * 0.05, "gravity": sim.CFG["weapons"]["mortar"]["gravity"],
              "mechs": mechs, "ours": ours, "frames": rec.frames, "events": rec.ev,
              "result": {"ours": [{"id": k, "points": r[0], "kills": r[1], "weapons": r[4], "max_ms": r[5]} for k, r in zip(ours, res)],
                         "hunters_mean": float(np.mean(hunter_pts))}}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        json.dump(replay, f, separators=(",", ":"))
    print(f"{mode} seed {seed}: ours {res} | {len(rec.frames)} frames, {os.path.getsize(out) / 1e6:.1f} MB, {time.time() - t0:.0f}s -> {out}")


if __name__ == "__main__":
    main()
