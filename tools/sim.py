"""Crude local simulator for mechfight/1 (not the real server). For testing bot logic only.

usage: python3 sim.py [practice|ffa] [n_matches] [seed]
"""
import copy, gzip, json, math, os, random, sys, time
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import mechfight as mf
from geo import Geo

CFG = None
for line in gzip.open(os.environ.get("MECH_STREAM", os.path.join(HERE, "..", "..", "mechfight-kit", "check", "stream.jsonl.gz"))):
    CFG = json.loads(line)["config"]
    break
TAU = 2 * math.pi


def wrap(a):
    return (a + math.pi) % TAU - math.pi


class M:  # mech state
    pass


class Sim:
    def __init__(self, geo, seed, bots):
        self.g = geo
        self.rng = random.Random(seed)
        self.cfg = CFG
        self.t = 0.0
        self.tick = 0
        mc = CFG["mech"]
        self.mechs = []
        free_cells = np.argwhere(geo.free)
        self.free_cells = free_cells
        zc = CFG["zone"]["start_center"]
        spawns = []
        while len(spawns) < 16:
            j, i = free_cells[self.rng.randrange(len(free_cells))]
            x, y = geo.cell_xy(j, i)
            if math.hypot(x - zc[0], y - zc[1]) > 560 * 0.8:
                continue
            if all(math.hypot(x - a, y - b) > 110 for a, b in spawns):
                spawns.append((x, y))
        for k, b in enumerate(bots):
            m = M()
            m.id = k; m.bot = b; m.pos = [spawns[k][0], spawns[k][1], geo.ground(*spawns[k])]
            m.vel = [0.0, 0.0, 0.0]; m.aim = [self.rng.uniform(-3, 3), 0.0]; m.cmd_aim = list(m.aim)
            m.hp = mc["hp"]; m.max_hp = mc["hp"]; m.energy = 100.0; m.cubes = 0
            m.weapons = [self.new_weapon("gun"), None]
            m.alive = True; m.last_dmg = -99; m.last_spend = -99; m.events = []; m.sounds = []
            m.act = {}; m.seen = {}; m.offer = None; m.upgrades = []; m.place = None; m.kills = 0
            m.grounded = True; m.zdmg = 0
            self.mechs.append(m)
        self.projectiles = []
        self.pid = 0
        self.pickups = {}
        self.crates = {}
        self.cubes = {}
        self.npcs = []
        for _ in range(CFG["loot"]["floor_count"]):
            self.add_pickup(self.rand_free(), self.rng.choice(["mortar", "laser", "lightning"]))
        for c in range(CFG["crates"]["count"]):
            p = self.rand_free()
            self.crates[1000 + c] = {"id": 1000 + c, "pos": p, "hp": 300}
        self.next_cube = 5000
        self.alive_count = 16
        self.deaths = []
        self.zone_phase = -1
        self.zone_setup()

    def rand_free(self):
        j, i = self.free_cells[self.rng.randrange(len(self.free_cells))]
        x, y = self.g.cell_xy(j, i)
        return [x, y, self.g.ground(x, y)]

    def add_pickup(self, p, kind):
        pid = 8000 + len(self.pickups) + self.rng.randrange(1000) * 0 + self.pid * 0
        while pid in self.pickups:
            pid += 1
        self.pickups[pid] = {"id": pid, "kind": kind, "pos": [p[0], p[1], p[2]]}

    def new_weapon(self, kind):
        st = dict(self.cfg["weapons"][kind])
        w = {"kind": kind, "cooldown": 0.0, "stats": st}
        if kind == "gun":
            w.update(ammo=st["mag"], reload=0.0)
        if kind == "laser":
            w.update(charge=0.0)
        return w

    # ---------------- zone
    def zone_setup(self):
        zc = self.cfg["zone"]
        self.zphases = []
        c = list(zc["start_center"]); r = zc["start_radius"]; t = 0
        for ph in zc["phases"]:
            nr = ph["radius"]
            a = self.rng.uniform(0, TAU); d = self.rng.uniform(0, r - nr) * 0.8
            nc = [c[0] + math.cos(a) * d, c[1] + math.sin(a) * d]
            # keep the centre on walkable ground
            j, i = self.g.nearest_free(nc[0], nc[1], 40)
            nc = list(self.g.cell_xy(j, i))
            if math.hypot(nc[0] - c[0], nc[1] - c[1]) > r - nr:
                nc = [c[0], c[1]]
            self.zphases.append(dict(c=c, r=r, nc=nc, nr=nr, ss=t + ph["hold_s"], se=t + ph["hold_s"] + ph["shrink_s"], dps=ph["dps"]))
            t += ph["hold_s"] + ph["shrink_s"]
            c, r = nc, nr

    def zone(self):
        for z in self.zphases:
            if self.t < z["se"]:
                return z
        z = dict(self.zphases[-1]); z["c"] = z["nc"]; z["r"] = 0; return z

    def zone_now(self):
        z = self.zone()
        if self.t < z["ss"]:
            return z["c"], z["r"], z
        f = min(1.0, (self.t - z["ss"]) / max(1e-6, z["se"] - z["ss"]))
        return [z["c"][0] + (z["nc"][0] - z["c"][0]) * f, z["c"][1] + (z["nc"][1] - z["c"][1]) * f], z["r"] + (z["nr"] - z["r"]) * f, z

    # ---------------- perception
    def can_see(self, m, o_pos, top):
        dx = o_pos[0] - m.pos[0]; dy = o_pos[1] - m.pos[1]
        d = math.hypot(dx, dy)
        eye = (m.pos[0], m.pos[1], m.pos[2] + 8)
        if d > 200:
            return False
        if d > 45:
            if abs(wrap(math.atan2(dy, dx) - m.aim[0])) > math.radians(60):
                return False
        return self.g.los(eye, (o_pos[0], o_pos[1], o_pos[2] + top * 0.5)) or self.g.los(eye, (o_pos[0], o_pos[1], o_pos[2] + top))

    def obs_for(self, m):
        cz, cr, z = self.zone_now()
        mechs = []
        for o in self.mechs:
            if o is m or not o.alive:
                continue
            if self.can_see(m, o.pos, 9):
                m.seen[o.id] = self.t
            if self.t - m.seen.get(o.id, -99) <= 5:
                mechs.append({"id": o.id, "kind": "mech", "pos": list(o.pos), "vel": list(o.vel), "aim": list(o.aim), "hp": o.hp,
                              "max_hp": o.max_hp, "cubes": o.cubes, "weapons": [w["kind"] for w in o.weapons if w],
                              "charging": any(w and w["kind"] == "laser" and w["charge"] > 0 for w in o.weapons)})
        vis = lambda p: math.hypot(p[0] - m.pos[0], p[1] - m.pos[1]) < 200 and self.can_see(m, p, 1)
        ev = m.events; m.events = []
        snd = m.sounds; m.sounds = []
        sw = []
        for w in m.weapons:
            sw.append(None if w is None else copy.deepcopy(w))
        hp = m.hp
        return {"type": "obs", "tick": self.tick, "t": round(self.t, 2), "alive": self.alive_count,
                "self": {"id": m.id, "pos": list(m.pos), "vel": list(m.vel), "aim": list(m.aim), "hp": hp, "max_hp": m.max_hp,
                         "energy": m.energy, "max_energy": 100, "grounded": m.grounded, "cubes": m.cubes, "upgrades": list(m.upgrades),
                         "in_zone": math.hypot(m.pos[0] - cz[0], m.pos[1] - cz[1]) <= cr, "weapons": sw,
                         "stats": {"walk_speed": 11.7, "boost_speed": 19.5, "turn_rate": 4, "muzzle_height": 7, "eye_height": 8}},
                "mechs": mechs,
                "projectiles": [{"id": p["id"], "kind": p["kind"], "pos": list(p["pos"]), "vel": list(p["vel"])} for p in self.projectiles if p["by"] != m.id and vis(p["pos"])],
                "crates": [dict(c) for c in self.crates.values() if vis(c["pos"])],
                "cubes": [dict(c) for c in self.cubes.values() if vis(c["pos"])],
                "pickups": [dict(p) for p in self.pickups.values() if vis(p["pos"])],
                "sounds": snd, "beams": [], "events": ev, "drops": [],
                "zone": {"center": z["c"], "radius": z["r"], "next_center": z["nc"], "next_radius": z["nr"],
                         "shrink_start": z["ss"], "shrink_end": z["se"], "dps": z["dps"]},
                "offer": m.offer}

    # ---------------- physics
    def blocked_at(self, x, y):
        g = self.g
        for a in range(0, 8):
            px = x + math.cos(a * TAU / 8) * 2.3; py = y + math.sin(a * TAU / 8) * 2.3
            i = int(round(px - g.x0)); j = int(round(py - g.y0))
            if not (0 <= i < g.nx and 0 <= j < g.ny):
                return True
            if self.blk[j, i]:
                return True
        return False

    def step_mech(self, m, dt):
        a = m.act
        mv = a.get("move", [0, 0]) or [0, 0]
        mag = math.hypot(*mv)
        if mag > 1:
            mv = [mv[0] / mag, mv[1] / mag]
        sp = 11.7
        charging = any(w and w["kind"] == "laser" and w["charge"] > 0 for w in m.weapons)
        if a.get("boost") and m.energy > 0 and mag > 0.1:
            sp = 19.5; m.energy -= 17 * dt; m.last_spend = self.t
        if charging:
            sp *= 0.5
        tv = [mv[0] * sp, mv[1] * sp]
        acc = 58.5 * dt
        for k in (0, 1):
            dv = tv[k] - m.vel[k]
            m.vel[k] += max(-acc, min(acc, dv))
        nx = m.pos[0] + m.vel[0] * dt; ny = m.pos[1] + m.vel[1] * dt
        if not self.blocked_at(nx, ny):
            m.pos[0], m.pos[1] = nx, ny
        elif not self.blocked_at(nx, m.pos[1]):
            m.pos[0] = nx; m.vel[1] = 0
        elif not self.blocked_at(m.pos[0], ny):
            m.pos[1] = ny; m.vel[0] = 0
        else:
            m.vel[0] = m.vel[1] = 0
        gz = self.g.ground(m.pos[0], m.pos[1])
        if a.get("jump") and m.grounded:
            m.vel[2] = 11; m.grounded = False
        if not m.grounded:
            m.vel[2] -= 22 * dt
            m.pos[2] += m.vel[2] * dt
            if m.pos[2] <= gz:
                m.pos[2] = gz; m.vel[2] = 0; m.grounded = True
        else:
            m.pos[2] = gz
        if self.t - m.last_spend > 0.6:
            m.energy = min(100, m.energy + 18 * dt)
        # aim slew
        ca = a.get("aim") or m.cmd_aim
        m.cmd_aim = ca
        dy = wrap(ca[0] - m.aim[0]); mx = 4 * dt
        m.aim[0] = wrap(m.aim[0] + max(-mx, min(mx, dy)))
        dp = max(-0.7, min(1.3, ca[1])) - m.aim[1]; mx = 3 * dt
        m.aim[1] += max(-mx, min(mx, dp))

    def damage(self, tgt, amount, weapon, by, src_xy):
        if not tgt.alive:
            return
        rear = False
        if by is not None and src_xy is not None:
            b = math.atan2(src_xy[1] - tgt.pos[1], src_xy[0] - tgt.pos[0])
            if abs(wrap(b - tgt.aim[0])) > math.pi / 2:
                amount *= 1.5; rear = True
            sh = self.mechs[by]
            amount *= 1 + 0.04 * min(sh.cubes, 10)
            sh.events.append({"type": "hit", "target": tgt.id, "amount": amount, "weapon": weapon})
            sh.last_dmg = self.t
            tgt.events.append({"type": "damaged", "amount": amount, "weapon": weapon, "bearing": b, "by": by, "rear": rear})
        else:
            tgt.events.append({"type": "damaged", "amount": amount, "weapon": weapon, "bearing": None, "by": None})
        tgt.hp -= amount
        tgt.last_dmg = self.t
        if tgt.hp <= 0:
            tgt.alive = False
            tgt.place = self.alive_count
            self.deaths.append((self.t, tgt.id))
            if by is not None:
                self.mechs[by].events.append({"type": "kill", "target": tgt.id}); self.mechs[by].kills += 1
            for w in tgt.weapons:
                if w and w["kind"] != "gun":
                    self.add_pickup(tgt.pos, w["kind"])
            for k in range(tgt.cubes + 2):
                self.spawn_cube(tgt.pos)
            for o in self.mechs:
                if o.alive:
                    o.sounds.append({"kind": "death", "bearing": math.atan2(tgt.pos[1] - o.pos[1], tgt.pos[0] - o.pos[0]), "dist": "far", "age": 0})

    def damage_crate(self, c, amount):
        c["hp"] -= amount
        if c["hp"] <= 0:
            self.crates.pop(c["id"], None)
            self.spawn_cube(c["pos"])
            if self.rng.random() < 0.5:
                self.add_pickup(c["pos"], self.rng.choice(["mortar", "laser", "lightning"]))

    def spawn_cube(self, p):
        self.next_cube += 1
        self.cubes[self.next_cube] = {"id": self.next_cube, "pos": [p[0] + self.rng.uniform(-2, 2), p[1] + self.rng.uniform(-2, 2), p[2]]}

    def hearers(self, pos, kind, rng_):
        for o in self.mechs:
            if o.alive:
                d = math.hypot(pos[0] - o.pos[0], pos[1] - o.pos[1])
                if 0.5 < d < rng_:
                    o.sounds.append({"kind": kind, "bearing": math.atan2(pos[1] - o.pos[1], pos[0] - o.pos[0]) + self.rng.gauss(0, 0.12),
                                     "dist": "near" if d < 60 else "mid" if d < 160 else "far", "age": 0})

    def fire(self, m, dt):
        f = m.act.get("fire") or [False, False]
        d = [math.cos(m.aim[1]) * math.cos(m.aim[0]), math.cos(m.aim[1]) * math.sin(m.aim[0]), math.sin(m.aim[1])]
        for i, w in enumerate(m.weapons):
            if not w:
                continue
            w["cooldown"] = max(0.0, w["cooldown"] - dt)
            st = w["stats"]
            on = i < len(f) and f[i]
            if w["kind"] == "gun":
                if w["reload"] > 0:
                    w["reload"] = max(0.0, w["reload"] - dt)
                    if w["reload"] == 0:
                        w["ammo"] = st["mag"]
                    continue
                if (m.act.get("reload") and w["ammo"] < st["mag"]) or w["ammo"] == 0:
                    w["reload"] = st["reload"]; continue
                if on and w["cooldown"] <= 0:
                    w["cooldown"] = st["cooldown"]; w["ammo"] -= 1
                    sp = math.radians(st["spread_deg"])
                    yaw = m.aim[0] + self.rng.gauss(0, sp); pit = m.aim[1] + self.rng.gauss(0, sp)
                    dd = [math.cos(pit) * math.cos(yaw), math.cos(pit) * math.sin(yaw), math.sin(pit)]
                    o = [m.pos[0] + dd[0] * 3, m.pos[1] + dd[1] * 3, m.pos[2] + 7 + dd[2] * 3]
                    self.pid += 1
                    self.projectiles.append({"id": self.pid, "kind": "gun", "pos": o, "vel": [dd[k] * st["speed"] for k in range(3)], "by": m.id,
                                             "dmg": st["damage"], "left": st["range"]})
                    self.hearers(m.pos, "gun", 320)
            elif w["kind"] == "mortar":
                if on and w["cooldown"] <= 0:
                    w["cooldown"] = st["cooldown"]
                    o = [m.pos[0] + d[0] * 3, m.pos[1] + d[1] * 3, m.pos[2] + 9.5 + d[2] * 3]
                    self.pid += 1
                    self.projectiles.append({"id": self.pid, "kind": "mortar", "pos": o, "vel": [d[k] * st["speed"] for k in range(3)], "by": m.id, "dmg": st["damage"], "left": 1e9, "ttl": 7})
                    self.hearers(m.pos, "mortar", 420)
            elif w["kind"] == "laser":
                if w["cooldown"] > 0:
                    w["charge"] = 0; continue
                if on:
                    w["charge"] += dt
                    if w["charge"] >= st["charge"] + st["hold_s"]:
                        on = False
                    else:
                        if self.tick % 10 == 0:
                            self.hearers(m.pos, "laser_charge", 140)
                        continue
                if not on and w["charge"] > 0:
                    if w["charge"] < st["min_charge"]:
                        w["charge"] += dt; continue
                    frac = min(1, w["charge"] / st["charge"])
                    dmg = st["damage"] * (st["min_frac"] + (1 - st["min_frac"]) * frac)
                    w["charge"] = 0; w["cooldown"] = st["cooldown"]
                    self.hitscan(m, d, st["range"], dmg, "laser")
                    self.hearers(m.pos, "laser", 380)
            elif w["kind"] == "lightning":
                if on and w["cooldown"] <= 0 and m.energy >= st["energy"]:
                    w["cooldown"] = st["cooldown"]; m.energy -= st["energy"]; m.last_spend = self.t
                    best = None
                    for o in self.mechs:
                        if o is m or not o.alive:
                            continue
                        dx = o.pos[0] - m.pos[0]; dy = o.pos[1] - m.pos[1]; dist = math.hypot(dx, dy)
                        if dist < st["range"] and abs(wrap(math.atan2(dy, dx) - m.aim[0])) < math.radians(st["cone_deg"]) and \
                                self.g.los((m.pos[0], m.pos[1], m.pos[2] + 7), (o.pos[0], o.pos[1], o.pos[2] + 4.5)):
                            if best is None or dist < best[0]:
                                best = (dist, o)
                    for c in self.crates.values():
                        dx = c["pos"][0] - m.pos[0]; dy = c["pos"][1] - m.pos[1]; dist = math.hypot(dx, dy)
                        if dist < st["range"] and abs(wrap(math.atan2(dy, dx) - m.aim[0])) < math.radians(st["cone_deg"]):
                            if best is None or dist < best[0]:
                                best = (dist, c)
                    if best:
                        if isinstance(best[1], dict):
                            self.damage_crate(best[1], st["damage"])
                        else:
                            self.damage(best[1], st["damage"], "lightning", m.id, m.pos)
                    self.hearers(m.pos, "lightning", 160)

    def hitscan(self, m, d, rng_, dmg, kind):
        o = (m.pos[0], m.pos[1], m.pos[2] + 7)
        best = None
        for t in self.mechs:
            if t is m or not t.alive:
                continue
            rel = [t.pos[0] - o[0], t.pos[1] - o[1]]
            s = rel[0] * d[0] + rel[1] * d[1]
            hor = math.hypot(d[0], d[1])
            if s <= 0:
                continue
            s3 = s / max(hor * hor, 1e-6)
            px = o[0] + d[0] * s3; py = o[1] + d[1] * s3; pz = o[2] + d[2] * s3
            if math.hypot(px - t.pos[0], py - t.pos[1]) < 2.5 and t.pos[2] - 0.2 < pz < t.pos[2] + 9.2 and s3 < rng_:
                if self.g.los(o, (px, py, pz)) and (best is None or s3 < best[0]):
                    best = (s3, t)
        if best:
            self.damage(best[1], dmg, kind, m.id, m.pos)

    def step_projectiles(self, dt):
        keep = []
        for p in self.projectiles:
            a = list(p["pos"])
            if p["kind"] == "mortar":
                p["vel"][2] -= 22 * dt
                p["ttl"] -= dt
            b = [a[k] + p["vel"][k] * dt for k in range(3)]
            p["left"] -= math.dist(a, b)
            hit = None
            for t in self.mechs:
                if not t.alive or t.id == p["by"]:
                    continue
                # closest approach of segment to vertical cylinder axis
                ax, ay = a[0] - t.pos[0], a[1] - t.pos[1]
                dx, dy = b[0] - a[0], b[1] - a[1]
                L2 = dx * dx + dy * dy
                s = 0 if L2 == 0 else max(0, min(1, -(ax * dx + ay * dy) / L2))
                cx, cy = ax + dx * s, ay + dy * s
                cz = a[2] + (b[2] - a[2]) * s
                if math.hypot(cx, cy) < 2.5 + 0.3 and t.pos[2] - 0.3 < cz < t.pos[2] + 9.3:
                    hit = (t, [a[k] + (b[k] - a[k]) * s for k in range(3)])
                    break
            blocked = not self.g.los(a, b, step=1.0) or b[2] < self.g.ground(b[0], b[1])
            if hit and (not blocked or self.g.los(a, hit[1], 1.0)):
                if p["kind"] == "gun":
                    self.damage(hit[0], p["dmg"], "gun", p["by"], a)
                else:
                    self.explode(p, hit[1], direct=hit[0])
                continue
            if blocked or p["left"] <= 0 or p.get("ttl", 1) <= 0:
                if p["kind"] == "mortar":
                    self.explode(p, b)
                continue
            # crates
            if p["kind"] == "gun":
                hc = None
                for c in self.crates.values():
                    if math.hypot(b[0] - c["pos"][0], b[1] - c["pos"][1]) < 2 and c["pos"][2] - 0.5 < b[2] < c["pos"][2] + 2.6:
                        hc = c; break
                if hc:
                    self.damage_crate(hc, p["dmg"]); continue
            p["pos"] = b
            keep.append(p)
        self.projectiles = keep

    def explode(self, p, at, direct=None):
        st = self.cfg["weapons"]["mortar"]
        self.hearers(at, "explosion", 420)
        for t in self.mechs:
            if not t.alive:
                continue
            d = math.dist(at, (t.pos[0], t.pos[1], t.pos[2] + 4.5)) - 2.5
            dmg = 0
            if t is direct:
                dmg += st["damage"]
            if d < st["splash_radius"]:
                f = 1 - (1 - st["splash_edge"]) * max(0, d) / st["splash_radius"]
                dmg += st["splash"] * f
            if dmg > 0:
                if t.id == p["by"]:
                    dmg *= 0.5
                    self.damage(t, dmg, "mortar", None, None)
                else:
                    self.damage(t, dmg, "mortar", p["by"], at)

    def step(self):
        dt = 0.05
        if self.tick % 2 == 0:
            # decisions
            for m in self.mechs:
                if not m.alive:
                    continue
                o = self.obs_for(m)
                t0 = time.perf_counter()
                try:
                    a = m.bot.act(o) or {}
                except Exception as e:
                    import traceback; traceback.print_exc()
                    a = {}
                m.dt_ms = (time.perf_counter() - t0) * 1000
                m.maxms = max(getattr(m, "maxms", 0), m.dt_ms)
                m.totms = getattr(m, "totms", 0) + m.dt_ms; m.nact = getattr(m, "nact", 0) + 1
                m.act = a
                if a.get("pick") is not None and m.offer:
                    ch = m.offer["choices"][a["pick"]]
                    self.apply_upgrade(m, ch["id"]); m.offer = None
                if a.get("take") is not None:
                    p = self.pickups.get(a["take"])
                    if p and math.hypot(p["pos"][0] - m.pos[0], p["pos"][1] - m.pos[1]) < 4 and p["kind"] not in [w["kind"] for w in m.weapons if w]:
                        slot = a.get("drop") if a.get("drop") is not None else (1 if m.weapons[1] is None else (0 if m.weapons[0] is None else None))
                        if slot is not None:
                            old = m.weapons[slot]
                            if old:
                                self.add_pickup(m.pos, old["kind"])
                            w = self.new_weapon(p["kind"]); w["cooldown"] = max(1.0, w["stats"]["cooldown"])
                            m.weapons[slot] = w
                            self.pickups.pop(p["id"])
                            m.events.append({"type": "took", "id": p["id"], "kind": p["kind"], "slot": slot})
                        else:
                            m.events.append({"type": "take_failed", "id": a["take"], "reason": "full"})
                    else:
                        m.events.append({"type": "take_failed", "id": a["take"], "reason": "range"})
                    m.act = dict(a); m.act.pop("take", None)
        for m in self.mechs:
            if m.alive:
                self.step_mech(m, dt)
                self.fire(m, dt)
                if m.act.get("jump"):
                    m.act = dict(m.act); m.act["jump"] = False
                for cid, c in list(self.cubes.items()):
                    if math.hypot(c["pos"][0] - m.pos[0], c["pos"][1] - m.pos[1]) < 3.5:
                        self.cubes.pop(cid)
                        m.cubes += 1
                        if m.cubes <= 10:
                            m.max_hp *= 1.04 / (1 + 0.04 * (m.cubes - 1)) * (1 + 0.04 * (m.cubes - 1)) if False else 1.04
                            m.hp += m.max_hp - m.max_hp / 1.04
                        m.events.append({"type": "pickup", "cubes": m.cubes})
                        if m.cubes in (1, 3, 6, 10, 15) and m.offer is None:
                            ids = ["plating", "armor", "nanites", "servos", "gun_damage", "sensors"]
                            self.rng.shuffle(ids)
                            m.offer = {"id": m.cubes, "expires": self.t + 5, "choices": [{"id": i, "name": i, "desc": i} for i in ids[:3]]}
        self.step_projectiles(dt)
        cz, cr, z = self.zone_now()
        for m in self.mechs:
            if not m.alive:
                continue
            if math.hypot(m.pos[0] - cz[0], m.pos[1] - cz[1]) > cr:
                self.damage(m, z["dps"] * dt, "zone", None, None)
            elif self.t - m.last_dmg > 5 and m.hp < m.max_hp:
                m.hp = min(m.max_hp, m.hp + 45 * dt)
        self.alive_count = sum(m.alive for m in self.mechs)
        self.t += dt
        self.tick += 1

    def apply_upgrade(self, m, uid):
        m.upgrades.append(uid)
        if uid == "plating":
            m.max_hp *= 1.15; m.hp *= 1.15


class Hunter:
    """Stand-in house bot: gun + loot, hunts a target it is told about, aims with noise."""

    def __init__(self, geo, skill, rng, squad):
        self.g = geo; self.skill = skill; self.rng = rng; self.squad = squad
        self.err = {0: 0.05, 1: 0.03, 2: 0.015, 3: 0.008}[skill]
        self.active = False
        self.strafe = 1; self.su = 0

    def act(self, o):
        me = o["self"]; x, y, z = me["pos"]
        a = {"tick": o["tick"]}
        if o["offer"]:
            a["pick"] = 0
        enemies = [m for m in o["mechs"] if m["kind"] == "mech" and m["id"] not in self.squad["members"]]
        tgt = None
        if any(e["type"] == "damaged" and e.get("by") is not None for e in o["events"]):
            self.provoked = o["t"]
        if not self.active and o["t"] - getattr(self, "provoked", -99) > 6:
            enemies = []
        if enemies:
            tgt = min(enemies, key=lambda m: math.dist(m["pos"], me["pos"]))
            self.squad["known"] = (tgt["id"], tgt["pos"], o["t"])
        kinds = [w["kind"] if w else None for w in me["weapons"]]
        for p in o["pickups"]:
            if math.hypot(p["pos"][0] - x, p["pos"][1] - y) < 3.8 and p["kind"] not in kinds and kinds[1] is None:
                a["take"] = p["id"]
        # movement
        zc = o["zone"]["next_center"]; zr = o["zone"]["next_radius"]
        goal = None
        if self.active and self.squad.get("known") and o["t"] - self.squad["known"][2] < 30:
            goal = self.squad["known"][1]
        elif kinds[1] is None and o["pickups"]:
            p = min(o["pickups"], key=lambda p: math.dist(p["pos"], me["pos"]))
            goal = p["pos"]
        if not me["in_zone"] or math.hypot(x - zc[0], y - zc[1]) > zr * 0.8:
            goal = zc
        mv = [0, 0]
        if goal is not None and math.hypot(goal[0] - x, goal[1] - y) > 3:
            self.g.budget = 1
            f = self.g.field(round(goal[0] / 16) * 16, round(goal[1] / 16) * 16, x, y)
            v = self.g.steer(f, x, y)[0] if f is not None else None
            if v:
                mv = list(v)
        if tgt is not None:
            d = math.dist(tgt["pos"], me["pos"])
            if o["t"] > self.su:
                self.strafe = self.rng.choice((-1, 1)); self.su = o["t"] + self.rng.uniform(0.6, 1.6)
            bx, by = (tgt["pos"][0] - x) / d, (tgt["pos"][1] - y) / d
            rad = 0.7 if d > 70 else (-0.5 if d < 30 else 0)
            if not me["in_zone"]:
                pass
            else:
                mv = [-by * self.strafe + bx * rad, bx * self.strafe + by * rad]
            # aim with lead + noise
            t_ = d / 252
            p = [tgt["pos"][0] + tgt["vel"][0] * t_, tgt["pos"][1] + tgt["vel"][1] * t_, tgt["pos"][2] + 4.5]
            yaw = math.atan2(p[1] - y, p[0] - x) + self.rng.gauss(0, self.err)
            pit = math.atan2(p[2] - z - 7, math.hypot(p[0] - x, p[1] - y)) + self.rng.gauss(0, self.err)
            a["aim"] = [yaw, pit]
            f = [False, False]
            for i, w in enumerate(me["weapons"]):
                if not w:
                    continue
                if w["kind"] == "gun":
                    f[i] = d < 200
                elif w["kind"] == "laser":
                    f[i] = not (w.get("charge", 0) >= w["stats"]["charge"] and abs(wrap(me["aim"][0] - yaw)) < 0.02)
                elif w["kind"] == "lightning":
                    f[i] = d < 43
                elif w["kind"] == "mortar":
                    if 30 < d < 220:
                        th = 0.5 * math.asin(min(1, d * 22 / 72 ** 2))
                        a["aim"] = [yaw, th]
                        f[i] = abs(me["aim"][1] - th) < 0.03
            a["fire"] = f
        else:
            a["aim"] = [math.atan2(mv[1], mv[0]) if any(mv) else me["aim"][0], 0]
        a["move"] = mv
        return a


def run_match(mode, seed, geo, verbose=False):
    global HGEO
    HGEO = HGEO or geo
    import importlib
    import bot as botmod
    if os.environ.get("SIM_BOT") == "example":
        sys.path.insert(0, os.environ.get("EXAMPLE_DIR", "/tmp")); import example_bot as botmod
    rng = random.Random(seed)
    hello = {"type": "hello", "protocol": "mechfight/1", "slot": 0, "seed": seed, "map": {"dir": os.path.join(HERE, "arena")}, "config": CFG}
    bots = []
    squad = {"members": set(), "known": None}
    ours = []
    if mode == "practice":
        me_slot = rng.randrange(16)
        skills = [0] * 4 + [1] * 4 + [2] * 4 + [3] * 3
        for k in range(16):
            if k == me_slot:
                b = botmod.Bot(dict(hello, slot=k)); ours.append(k)
            else:
                b = Hunter(HGEO, skills.pop(0), random.Random(seed * 31 + k), squad); squad["members"].add(k)
            bots.append(b)
    else:  # ffa: 8 of ours, 8 expert-ish hunters that do not cooperate
        for k in range(16):
            if k % 2 == 0:
                b = botmod.Bot(dict(hello, slot=k)); ours.append(k)
            else:
                b = Hunter(HGEO, 3, random.Random(seed * 31 + k), {"members": {k}, "known": None}); b.active = False
            bots.append(b)
    sim = Sim(geo, seed, bots)
    sim.blk = BLK
    hunters = [b for b in bots if isinstance(b, Hunter)]
    if mode == "practice":
        hunters.sort(key=lambda h: h.skill)
        hunters[0].active = True
    while sim.t < 480 and sim.alive_count > 1 and any(sim.mechs[k].alive for k in ours if mode == "practice") | (mode != "practice"):
        if mode == "practice":
            dead_h = sum(1 for k, b in enumerate(bots) if isinstance(b, Hunter) and not sim.mechs[k].alive)
            n_act = 1 + dead_h + (15 if sim.t > 300 else 0) + int(sim.t // 60)
            for h in [h for h in hunters if sim.mechs[bots.index(h)].alive][:n_act]:
                h.active = True
                sim.squad_target = ours[0]
            # squad shares sight: hunters know our position when any of them sees us
            for k in ours:
                m = sim.mechs[k]
                for hk, h in enumerate(bots):
                    if isinstance(h, Hunter) and sim.mechs[hk].alive and m.alive and sim.can_see(sim.mechs[hk], m.pos, 9):
                        squad["known"] = (k, list(m.pos), sim.t)
                        break
        sim.step()
    # placements
    alive = [m for m in sim.mechs if m.alive]
    alive.sort(key=lambda m: -m.hp)
    for r, m in enumerate(alive):
        m.place = r + 1
    res = []
    for k in ours:
        m = sim.mechs[k]
        res.append((16 - m.place, m.kills, round(sim.t), m.cubes, [w["kind"] for w in m.weapons if w], round(getattr(m, "maxms", 0), 1),
                    round(getattr(m, "totms", 0) / max(1, getattr(m, "nact", 1)), 2)))
    hunter_pts = [16 - sim.mechs[k].place for k, b in enumerate(bots) if isinstance(b, Hunter)]
    return res, hunter_pts


BLK = None
HGEO = None
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "practice"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    seed0 = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    geo = Geo(os.path.join(HERE, "arena"))
    HGEO = Geo(os.path.join(HERE, "arena")); HGEO.cache_n = 150
    BLK = np.load(os.path.join(HERE, "map.npz"))["blocked"]
    tot = []
    for s in range(seed0, seed0 + n):
        t0 = time.time()
        res, hp = run_match(mode, s, geo)
        pts = [r[0] for r in res]
        tot += pts
        print(f"seed {s}: ours {res}  hunters mean {np.mean(hp):.2f}  ({time.time() - t0:.0f}s)", flush=True)
    print("MEAN", np.mean(tot))
