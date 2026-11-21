"""Survivor bot for Mech Royale (mechfight/1).

Priorities: stay inside the zone, stay near cover, loot early, fight facing the enemy while strafing,
break line of sight when losing, and bank HP (cubes, plating) for the end-game zone.
"""
import math, os, random, sys, time
import mechfight as mf
from geo import Geo

TAU = 2 * math.pi
WEAPON_RANK = {"laser": 3, "lightning": 2, "mortar": 1, "gun": 0}
UPGRADE_PREF = ["plating", "armor", "nanites", "laser_damage", "laser_charge", "gun_damage", "gun_rate",
                "lightning_efficiency", "servos", "gun_mag", "lightning_range", "capacitor", "gun_velocity",
                "reactor", "jets", "gyros", "sensors", "mortar_reload", "mortar_splash", "mortar_velocity",
                "lightning_chain", "laser_pierce"]


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def d2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Bot:
    name = "survivor"

    def __init__(self, hello):
        t0 = time.time()
        self.cfg = hello["config"]
        random.seed(hello.get("seed", 0))
        self.geo = Geo(hello["map"]["dir"])
        mf.log(f"geo ready in {time.time() - t0:.2f}s")
        self.wcfg = self.cfg["weapons"]
        self.mech = self.cfg["mech"]
        self.vis = self.cfg["vision"]
        self.reach = self.cfg["loot"]["pickup_radius"]
        self.strafe_dir = 1
        self.strafe_until = 0.0
        self.last_pos = None
        self.stuck_ticks = 0
        self.unstick_until = 0.0
        self.unstick_dir = None
        self.last_mode = "idle"
        self.prog = []           # (t, x, y) while travelling outside combat, to catch back-and-forth loops
        # behaviour switches, all OFF by default (set SURVIVOR_FLAGS, e.g. "cover,creep", to try them in tools/sim.py):
        #   pick  = fight only mechs that threaten us; otherwise face them and slip into cover
        #   cover = route cover-to-cover (open ground costs more) when the zone isn't pressing
        #   creep = walk below the footstep threshold when an enemy was near recently and the zone allows it
        # Sim A/B (120 practice + 15 ffa seeds): pick -0.9 pts/match (significant), cover and creep -0.2..-0.3 (noise;
        # the sim has no canopy or sound, so it can only show their costs). See the review notes before turning any on.
        fl = os.environ.get("SURVIVOR_FLAGS", "")
        self.flags = set(f.strip() for f in fl.split(",") if f.strip())
        self.contact_t = -99.0   # last time an enemy was seen within 70 m or heard near
        self.threat_bearing = None
        self.threat_until = 0.0
        self.attackers = {}      # id -> last time they damaged us
        self.sound_bearing = None
        self.sound_until = 0.0
        self.hide = None         # (x, y) chosen hiding spot
        self.hide_key = None
        self.hide_time = -99
        self.blacklist = {}      # pickup/cube/crate id -> expiry time
        self.goal_desc = ""
        self.scan_phase = random.random() * TAU
        self.last_take = -1
        self.jump_next = False
        self.cur_target = None
        self.cur_target_t = -99
        self.flee_until = 0.0

    # ------------------------------------------------------------------ helpers
    def eye(self, me):
        return (me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"].get("eye_height", 8))

    def muzzle(self, me):
        return (me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"].get("muzzle_height", 7))

    def zone_info(self, obs):
        z = obs["zone"]
        t = obs["t"]
        c, r = z["center"], z["radius"]
        nc, nr = z["next_center"], z["next_radius"]
        ss, se = z["shrink_start"], z["shrink_end"]
        if ss <= t < se and se > ss:
            f = (t - ss) / (se - ss)
            cur_c = (c[0] + (nc[0] - c[0]) * f, c[1] + (nc[1] - c[1]) * f)
            cur_r = r + (nr - r) * f
        else:
            cur_c, cur_r = c, r
        return cur_c, cur_r, nc, nr, ss, se

    def choose_hide(self, me, obs, nc, nr):
        """Pick a covered spot inside the next circle, reachable and not too far."""
        import numpy as np
        g = self.geo
        x, y = me["pos"][0], me["pos"][1]
        margin = min(30.0, nr * 0.35) + 4
        rr = max(nr - margin, 3.0)
        if nr < 40:
            j, i = g.nearest_free(nc[0], nc[1], 40)
            return g.cell_xy(j, i)
        if d2((x, y), nc) - rr > 200:
            # far away: head for the near edge of the circle; refine once closer
            dd = max(d2((x, y), nc), 1e-3)
            px, py = nc[0] + (x - nc[0]) / dd * rr * 0.8, nc[1] + (y - nc[1]) / dd * rr * 0.8
            j, i = g.nearest_free(px, py, 40)
            self.hide_time = obs["t"] - 30  # re-evaluate in ~10 s
            return g.cell_xy(j, i)
        df = g.dist_field_from(x, y, limit=min(d2((x, y), nc) + rr + 60, 420))
        H, W = g.H, g.W
        # cell centre coordinates
        ii = np.arange(W) * 2 + g.x0 + 1
        jj = np.arange(H) * 2 + g.y0 + 1
        dx = ii[None, :] - nc[0]
        dy = jj[:, None] - nc[1]
        inside = (dx * dx + dy * dy) <= rr * rr
        ok = inside & np.isfinite(df) & g.free
        if not ok.any():
            j, i = g.nearest_free(nc[0], nc[1], 40)
            return g.cell_xy(j, i)
        dc = np.sqrt(dx * dx + dy * dy) / max(rr, 1)
        score = g.cover * 60.0 - np.where(np.isfinite(df), df, 1e6) * 0.08 - dc * 12.0
        score = np.where(ok, score, -1e9)
        k = int(np.argmax(score))
        j, i = divmod(k, W)
        return g.cell_xy(j, i)

    def safe_dir(self, x, y, dx, dy, probe=5.0):
        """Rotate (dx,dy) until a few metres ahead is walkable."""
        g = self.geo
        base = math.atan2(dy, dx)
        for off in (0, 0.4, -0.4, 0.8, -0.8, 1.2, -1.2, 1.6, -1.6, 2.2, -2.2, math.pi):
            a = base + off
            if g.walk_line(x, y, x + math.cos(a) * probe, y + math.sin(a) * probe):
                return math.cos(a), math.sin(a)
        return dx, dy

    def nav_to(self, me, gx, gy, weighted=False):
        x, y = me["pos"][0], me["pos"][1]
        if d2((x, y), (gx, gy)) < 3:
            return (0.0, 0.0), 0.0
        if self.geo.walk_line(x, y, gx, gy) and (not weighted or d2((x, y), (gx, gy)) < 40):
            d = d2((x, y), (gx, gy))
            return ((gx - x) / d, (gy - y) / d), d
        f = self.geo.field(gx, gy, x, y, weighted=weighted)
        if f is None:
            d = d2((x, y), (gx, gy))
            ux, uy = self.safe_dir(x, y, (gx - x) / d, (gy - y) / d)
            return (ux, uy), d
        v, rem = self.geo.steer(f, x, y)
        if v is None:
            d = d2((x, y), (gx, gy))
            return ((gx - x) / d, (gy - y) / d), d
        return v, rem

    def lead(self, src, tgt, vel, speed):
        """Intercept point for a straight projectile."""
        p = list(tgt)
        for _ in range(3):
            t = math.dist(src, p) / speed
            p = [tgt[0] + vel[0] * t, tgt[1] + vel[1] * t, tgt[2] + vel[2] * t * 0.3]
        return p

    def aim_at(self, src, p):
        yaw = math.atan2(p[1] - src[1], p[0] - src[0])
        pitch = math.atan2(p[2] - src[2], math.hypot(p[0] - src[0], p[1] - src[1]))
        return [yaw, max(self.mech["min_pitch"], min(self.mech["max_pitch"], pitch))]

    def mortar_solution(self, src, tgt, vel, v, g, high):
        p = list(tgt)
        sol = None
        for _ in range(3):
            R = math.hypot(p[0] - src[0], p[1] - src[1])
            dz = p[2] - src[2]
            disc = v ** 4 - g * (g * R * R + 2 * dz * v * v)
            if disc < 0 or R < 1:
                return None
            sq = math.sqrt(disc)
            th = math.atan((v * v + sq) / (g * R)) if high else math.atan((v * v - sq) / (g * R))
            T = R / (v * math.cos(th))
            sol = (math.atan2(p[1] - src[1], p[0] - src[0]), th, T)
            p = [tgt[0] + vel[0] * T, tgt[1] + vel[1] * T, tgt[2]]
        if sol and not (self.mech["min_pitch"] <= sol[1] <= self.mech["max_pitch"]):
            return None
        return sol

    # ------------------------------------------------------------------ main
    def act(self, obs):
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        act = {"tick": obs["tick"]}
        self.geo.budget = 1
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        weapons = me["weapons"]
        kinds = [w["kind"] if w else None for w in weapons]

        # ---- offers
        if obs.get("offer"):
            ch = obs["offer"]["choices"]
            best = min(range(len(ch)), key=lambda k: UPGRADE_PREF.index(ch[k]["id"]) if ch[k]["id"] in UPGRADE_PREF else 99)
            act["pick"] = best

        # ---- events
        for e in obs.get("events", []):
            if e["type"] == "damaged" and e.get("bearing") is not None and e.get("weapon") != "zone":
                self.threat_bearing = e["bearing"]
                self.threat_until = t + 2.5
                if e.get("by") is not None:
                    self.attackers[e["by"]] = t
            elif e["type"] == "take_failed":
                self.blacklist[e["id"]] = t + 20
        for s in obs.get("sounds", []):
            if s["kind"] in ("gun", "laser", "laser_charge", "lightning", "mortar", "step", "boost", "jet", "reload") and s["dist"] in ("near", "mid"):
                if t > self.sound_until or s["dist"] == "near":
                    self.sound_bearing = s["bearing"]
                    self.sound_until = t + 1.5

        # ---- stuck detection
        speed = math.hypot(me["vel"][0], me["vel"][1])
        if self.last_pos is not None and getattr(self, "last_move_mag", 0) > 0.5 and me["grounded"] and speed < 1.5:
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = max(0, self.stuck_ticks - 1)
        self.last_pos = (x, y)
        # no net progress: moving at speed but shuttling back and forth (speed alone never shows that)
        looping = False
        if getattr(self, "last_move_mag", 0) > 0.5 and self.last_mode not in ("fight", "flee", "idle", "hide") and t > self.unstick_until:
            self.prog.append((t, x, y))
            while self.prog and t - self.prog[0][0] > 4.0:
                self.prog.pop(0)
            if t - self.prog[0][0] >= 3.9 and d2((x, y), self.prog[0][1:]) < 4.0:
                looping = True
        else:
            self.prog = []
        if looping or (self.stuck_ticks > 8 and t > self.unstick_until):
            self.prog = []
            self.unstick_until = t + 1.2
            a = random.random() * TAU
            self.unstick_dir = (math.cos(a), math.sin(a))
            self.stuck_ticks = 0

        # ---- zone
        cur_c, cur_r, nc, nr, ss, se = self.zone_info(obs)
        dcur = d2((x, y), cur_c)
        in_zone = me["in_zone"]
        dnext = d2((x, y), nc)
        margin = min(30.0, nr * 0.35)
        need_zone = (not in_zone) or (dnext > nr - margin * 0.3 and (t > ss - 25 or dnext > nr + 60)) or (dcur > cur_r - 8)

        hide_key = (round(nc[0]), round(nc[1]), round(nr))
        if self.hide is None or self.hide_key != hide_key or t - self.hide_time > 40:
            self.hide_time = t
            self.hide = self.choose_hide(me, obs, nc, nr)
            self.hide_key = hide_key

        # ---- perception
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        eye = self.eye(me)
        mz = self.muzzle(me)
        for m in mechs:
            m["d"] = math.dist(m["pos"], me["pos"])
            m["los"] = self.geo.los(mz, (m["pos"][0], m["pos"][1], m["pos"][2] + 4.5))
        for n in npcs:
            n["d"] = math.dist(n["pos"], me["pos"])
            n["los"] = self.geo.los(mz, (n["pos"][0], n["pos"][1], n["pos"][2] + 1.7))

        def threat_score(m):
            s = m["d"] + m["hp"] * 0.04
            if m["id"] in self.attackers and t - self.attackers[m["id"]] < 4:
                s -= 60
            if not m["los"]:
                s += 80
            if m["id"] == self.cur_target and t - self.cur_target_t < 3:
                s -= 45
            return s

        if any(m["d"] < 70 for m in mechs) or any(s["dist"] == "near" and s["kind"] != "death" for s in obs.get("sounds", [])):
            self.contact_t = t

        def threatening(m):
            """Worth fighting: it hit us lately, it's on top of us, it's aiming at us, or it's a cheap kill."""
            if m["id"] in self.attackers and t - self.attackers[m["id"]] < 5:
                return True
            if not m["los"]:
                return False
            if m["d"] < 40:  # inside its all-round proximity sensors: it sees us anyway
                return True
            to_us = math.atan2(y - m["pos"][1], x - m["pos"][0])
            facing = abs(wrap(m.get("aim", [99])[0] - to_us)) < 0.35
            if facing and (m.get("charging") or m["d"] < 130):
                return True
            if m["hp"] < 0.3 * m.get("max_hp", 1400) and m["d"] < 90 and hp_frac > 0.6:
                return True
            return False

        cands = [m for m in mechs if threatening(m)] if "pick" in self.flags else mechs
        target = min(cands, key=threat_score) if cands else None
        # a visible mech we chose not to fight: keep our front armour (and eyes) on it and get out of its sight
        watch = None
        if target is None and "pick" in self.flags:
            vis = [m for m in mechs if m["los"] and m["d"] < 180]
            watch = min(vis, key=lambda m: m["d"]) if vis else None
        if target is not None:
            self.cur_target = target["id"]; self.cur_target_t = t
        recent_att = [k for k, tt in self.attackers.items() if t - tt < 3]
        close_enemies = [m for m in mechs if m["d"] < 120]

        # ---- choose movement
        crept = False
        move = (0.0, 0.0)
        boost = False
        mode = "idle"
        aim = None
        shoot_target = None  # (pos, vel, kind, radius, height)

        best_rank = max(WEAPON_RANK.get(k, -1) for k in kinds if k)

        if target is not None:
            mode = "fight"
            shoot_target = target
            # desired range depends on loadout
            has = set(k for k in kinds if k)
            if "laser" in has:
                want_lo, want_hi = 60, 160
            elif "lightning" in has:
                want_lo, want_hi = 12, 32
            elif "mortar" in has:
                want_lo, want_hi = 50, 130
            else:
                want_lo, want_hi = 25, 70
            their = set(target.get("weapons", []))
            if "lightning" in their and "lightning" not in has:
                want_lo = max(want_lo, 55); want_hi = max(want_hi, 90)
            if "mortar" in their and "lightning" in has:
                want_hi = min(want_hi, 25)
            n_los = len([m for m in mechs if m["los"] and m["d"] < 160])
            losing = hp_frac < 0.35 and (target["hp"] > me["hp"] * 1.1 or n_los > 1)
            if len(recent_att) >= 2 or n_los >= 3:
                losing = True
            if hp_frac < 0.5 and target["hp"] > me["hp"] * 1.3:
                losing = True
            if losing:
                self.flee_until = t + 2.0
            elif t < self.flee_until:
                losing = True
            tx, ty = target["pos"][0], target["pos"][1]
            bx, by = (tx - x) / max(target["d"], 0.1), (ty - y) / max(target["d"], 0.1)
            if t > self.strafe_until:
                self.strafe_dir = random.choice((-1, 1))
                self.strafe_until = t + random.uniform(0.3, 0.9)
            px, py = -by * self.strafe_dir, bx * self.strafe_dir
            if losing:
                mode = "flee"
                spot = self.cover_from(me, [m for m in mechs if m["d"] < 200] or [target])
                if spot:
                    move, _ = self.nav_to(me, spot[0], spot[1])
                else:
                    move = (-bx + px * 0.5, -by + py * 0.5)
                boost = me["energy"] > 15
            else:
                radial = 0.0
                if target["d"] > want_hi:
                    radial = 0.8
                elif target["d"] < want_lo:
                    radial = -0.8
                move = (px + bx * radial, py + by * radial)
                boost = me["energy"] > 45 and target["d"] < 150
            if need_zone and (not in_zone or dcur > cur_r - 15):
                zv, _ = self.nav_to(me, self.hide[0], self.hide[1])
                move = (zv[0] * 1.0 + move[0] * 0.4, zv[1] * 1.0 + move[1] * 0.4)
                boost = me["energy"] > 10 if not in_zone else boost
        else:
            # remembered threats without sight: face them
            goal = None
            if need_zone:
                goal = self.hide; mode = "zone"
            else:
                goal = self.loot_goal(me, obs, npcs, kinds, t, cur_c, cur_r)
                if goal is None:
                    goal = self.hide; mode = "hide"
                else:
                    mode = goal[2]
            zone_calm = in_zone and dcur < cur_r - 30 and not need_zone
            if watch is not None and mode != "zone" and watch["d"] < 140:
                spot = self.cover_from(me, [watch])
                if spot:
                    goal = (spot[0], spot[1], "avoid"); mode = "avoid"
            if goal is not None:
                weighted = "cover" in self.flags and mode not in ("zone", "avoid") and zone_calm
                mv, rem = self.nav_to(me, goal[0], goal[1], weighted=weighted)
                move = mv
                if mode == "avoid" and rem < 2:
                    move = (0.0, 0.0)
                if mode == "hide" and rem < 4:
                    move = (0.0, 0.0)
                if mode == "zone" and not in_zone:
                    boost = me["energy"] > 10
                # sneak when there's time: walk under the step-sound threshold
                if mode in ("hide",) and rem < 60 and in_zone:
                    k = 4.6 / max(me["stats"].get("walk_speed", 11.7), 1)
                    move = (move[0] * k, move[1] * k)
                # creep: footsteps start at sound.step_speed and carry sound.range.step (40 m); only worth the lost
                # speed when someone was close lately and the zone leaves time for it
                elif "creep" in self.flags and mode not in ("zone",) and zone_calm and t - self.contact_t < 6:
                    lim = 0.92 * self.cfg.get("sound", {}).get("step_speed", 5.0)
                    k = min(1.0, lim / max(me["stats"].get("walk_speed", 11.7), 1))
                    move = (move[0] * k, move[1] * k)
                    boost = False
                    crept = True
            if mode in ("npc", "crate") and self.loot_target is not None:
                shoot_target = self.loot_target

        if t < self.unstick_until and self.unstick_dir:
            move = self.unstick_dir
            if self.stuck_ticks == 0 and me["grounded"]:
                act["jump"] = True
            boost = me["energy"] > 20

        # wall-avoid
        mag = math.hypot(*move)
        if mag > 0.05:
            ux, uy = self.safe_dir(x, y, move[0] / mag, move[1] / mag)
            move = (ux * min(mag, 1.0), uy * min(mag, 1.0))
        self.last_move_mag = math.hypot(*move)
        act["move"] = [round(move[0], 3), round(move[1], 3)]
        act["boost"] = bool(boost)

        # ---- pickups (take when in range)
        self.try_take(me, obs, kinds, act, t)

        # ---- aiming and firing
        fire = [False, False]
        if shoot_target is not None:
            aim, fire = self.combat(me, obs, shoot_target, kinds, mechs, npcs)
        if aim is None and watch is not None:
            aim = self.aim_at(eye, (watch["pos"][0], watch["pos"][1], watch["pos"][2] + 4.5))
        if aim is None:
            aim = self.scan_aim(me, obs, move, t)
        act["aim"] = [round(aim[0], 4), round(aim[1], 4)]
        # keep laser charge only while it makes sense
        for i, w in enumerate(weapons):
            if w and w["kind"] == "laser" and w.get("charge", 0) > 0 and not fire[i] and shoot_target is None:
                fire[i] = w["charge"] < w["stats"].get("charge", 0.75) + 1.5  # hold rather than waste
        act["fire"] = fire
        g = weapons[0] if kinds[0] == "gun" else (weapons[1] if kinds[1] == "gun" else None)
        if g and g["reload"] == 0 and g["ammo"] < g["stats"]["mag"] * 0.5 and not any(fire) and mode not in ("fight", "flee"):
            act["reload"] = True
        self.last_mode = mode
        mf.draw.label(f"{mode}{'~' if crept else ''} hp{me['hp']:.0f} e{me['energy']:.0f}")
        return act

    # ------------------------------------------------------------------ sub-behaviours
    loot_target = None

    def loot_goal(self, me, obs, npcs, kinds, t, cur_c, cur_r):
        """Returns (x, y, mode) of something worth walking to, or None."""
        x, y, z = me["pos"]
        hp_frac = me["hp"] / max(1, me["max_hp"])
        self.loot_target = None
        best = None

        def inzone(p, pad=15):
            return d2(p, cur_c) < cur_r - pad

        def reachable(p):
            return abs(p[2] - self.geo.ground(p[0], p[1])) < 2.5 and self.geo.is_free(p[0], p[1]) or d2(p, (x, y)) < 3

        # weapons
        have = [k for k in kinds if k]
        worst = min((WEAPON_RANK[k] for k in have if k != "gun"), default=-1)
        for p in obs.get("pickups", []):
            if self.blacklist.get(p["id"], 0) > t or p["kind"] in have:
                continue
            r = WEAPON_RANK.get(p["kind"], -1)
            if kinds[1] is not None and r <= worst:
                continue
            d = d2(p["pos"], (x, y))
            if d < 90 and inzone(p["pos"]) and reachable(p["pos"]):
                sc = d - r * 15
                if best is None or sc < best[0]:
                    best = (sc, p["pos"][0], p["pos"][1], "weapon")
        # cubes
        for c in obs.get("cubes", []):
            if self.blacklist.get(c["id"], 0) > t:
                continue
            d = d2(c["pos"], (x, y))
            if d < 70 and inzone(c["pos"]) and reachable(c["pos"]):
                sc = d - 10
                if best is None or sc < best[0]:
                    best = (sc, c["pos"][0], c["pos"][1], "cube")
        # supply drops
        for dr in obs.get("drops", []):
            d = d2(dr["pos"], (x, y))
            if d < 160 and dr["lands_in"] < d / 11 + 3 and hp_frac > 0.3:
                if dr.get("holder") not in (None, me["id"]) and dr.get("progress", 0) > 2:
                    continue
                sc = d - 40
                if best is None or sc < best[0]:
                    best = (sc, dr["pos"][0], dr["pos"][1], "drop")
        if best is not None:
            return best[1], best[2], best[3]
        # NPC drones: farm cubes when healthy
        if hp_frac > 0.7:
            cands = [n for n in npcs if n["d"] < 90 and n["los"] and inzone(n["pos"], 5)]
            if cands:
                n = min(cands, key=lambda n: n["d"] + n["hp"] * 0.1)
                self.loot_target = n
                gx, gy = n["pos"][0], n["pos"][1]
                if n["d"] > 35:
                    return gx, gy, "npc"
                return x, y, "npc"
        # crates
        if hp_frac > 0.6:
            mz = self.muzzle(me)
            cands = []
            for c in obs.get("crates", []):
                d = d2(c["pos"], (x, y))
                if d < 70 and inzone(c["pos"], 5) and self.geo.los(mz, (c["pos"][0], c["pos"][1], c["pos"][2] + 1.2)):
                    cands.append((d, c))
            if cands:
                d, c = min(cands, key=lambda q: q[0])
                self.loot_target = {"pos": c["pos"], "vel": [0, 0, 0], "kind": "crate", "d": d, "hp": c["hp"], "id": c["id"], "los": True}
                if d > 40:
                    return c["pos"][0], c["pos"][1], "crate"
                return x, y, "crate"
        return None

    def try_take(self, me, obs, kinds, act, t):
        x, y, z = me["pos"]
        have = [k for k in kinds if k]
        worst_slot = None
        if kinds[1] is not None:
            # replace the worse non-gun weapon (slot 1 normally)
            cand = [(WEAPON_RANK.get(kinds[i], -1), i) for i in (0, 1) if kinds[i] and kinds[i] != "gun"]
            if cand:
                worst_slot = min(cand)[1]
        for p in obs.get("pickups", []):
            if p["kind"] in have or self.blacklist.get(p["id"], 0) > t:
                continue
            if d2(p["pos"], (x, y)) > self.reach - 0.3 or abs(p["pos"][2] - z) > 3.8:
                continue
            if kinds[1] is None or kinds[0] is None:
                act["take"] = p["id"]
                return
            if worst_slot is not None and WEAPON_RANK.get(p["kind"], -1) > WEAPON_RANK.get(kinds[worst_slot], -1):
                w = me["weapons"][worst_slot]
                if w["kind"] == "laser" and w.get("charge", 0) > 0:
                    continue
                act["take"] = p["id"]
                act["drop"] = worst_slot
                return

    def cover_from(self, me, enemies):
        """A nearby walkable spot with no line of sight from any of the enemies."""
        g = self.geo
        x, y, z = me["pos"]
        best = None
        cx = sum(m["pos"][0] for m in enemies) / len(enemies)
        cy = sum(m["pos"][1] for m in enemies) / len(enemies)
        away = math.atan2(y - cy, x - cx)
        for r in (12, 22, 35):
            for k in range(12):
                a = away + (k - 6) * (TAU / 12)
                px, py = x + math.cos(a) * r, y + math.sin(a) * r
                if not g.is_free(px, py) or not g.walk_line(x, y, px, py):
                    continue
                pz = g.ground(px, py) + 4.5
                hidden = True
                for m in enemies:
                    e = (m["pos"][0], m["pos"][1], m["pos"][2] + 8)
                    if g.los(e, (px, py, pz)):
                        hidden = False
                        break
                if hidden:
                    sc = r - math.cos(wrap(a - away)) * 10
                    if best is None or sc < best[0]:
                        best = (sc, px, py)
            if best:
                break
        return (best[1], best[2]) if best else None

    def scan_aim(self, me, obs, move, t):
        x, y, z = me["pos"]
        if t < self.threat_until and self.threat_bearing is not None:
            return [self.threat_bearing, 0.0]
        if t < self.sound_until and self.sound_bearing is not None:
            return [self.sound_bearing, 0.0]
        mag = math.hypot(*move)
        if mag > 0.3:
            base = math.atan2(move[1], move[0])
            return [wrap(base + 1.0 * math.sin(t * 2.0 + self.scan_phase)), 0.0]
        return [wrap(t * 1.8 + self.scan_phase), 0.0]

    def combat(self, me, obs, tg, kinds, mechs, npcs):
        weapons = me["weapons"]
        mz = self.muzzle(me)
        size = 1 + 0.05 * tg.get("cubes", 0) if tg.get("kind") == "mech" else 1.0
        if tg.get("kind") == "mech":
            ch = 4.5 * size
            rad = 2.5 * size
        elif tg.get("kind") == "npc":
            ch = 1.7; rad = 1.8
        else:
            ch = 1.2; rad = 1.4
        tp = (tg["pos"][0], tg["pos"][1], tg["pos"][2] + ch)
        vel = tg.get("vel", [0, 0, 0])
        dist = math.dist(mz, tp)
        los = tg.get("los", True)
        cur = me["aim"]
        fire = [False, False]
        aim = None
        primary = None
        # choose the weapon that sets the aim
        order = sorted([(i, w) for i, w in enumerate(weapons) if w], key=lambda iw: -self.weapon_value(iw[1]["kind"], dist, los, iw[1]))
        for i, w in order:
            k = w["kind"]; st = w["stats"]
            if k == "gun":
                if not los or dist > st["range"] * 0.95:
                    continue
                lp = self.lead(mz, tp, vel, st["speed"])
                aim = self.aim_at(mz, lp)
            elif k == "laser":
                if not los or dist > st["range"]:
                    continue
                aim = self.aim_at(mz, tp)
            elif k == "lightning":
                if not los or dist > st["range"] * 0.95:
                    continue
                aim = self.aim_at(mz, tp)
            elif k == "mortar":
                if dist < 22 or dist > 230:
                    continue
                src = (me["pos"][0], me["pos"][1], me["pos"][2] + 9.5)
                sol = self.mortar_solution(src, (tg["pos"][0], tg["pos"][1], tg["pos"][2] + 1), vel, st["speed"], st["gravity"], high=not los)
                if sol is None:
                    continue
                aim = [sol[0], sol[1]]
            primary = i
            break
        if aim is None:
            # no shot: face the target anyway (front armor, vision)
            aim = self.aim_at(mz, tp)
            if not los:
                aim[1] = 0.0
        yaw_err = abs(wrap(cur[0] - aim[0]))
        pitch_err = abs(cur[1] - aim[1])
        tol = math.atan2(rad * 0.8, max(dist, 1.0)) + 0.004
        for i, w in enumerate(weapons):
            if not w:
                continue
            k = w["kind"]; st = w["stats"]
            if k == "gun":
                if los and dist < st["range"] * 0.95 and w["cooldown"] <= 0.1 and w["ammo"] > 0 and w["reload"] == 0:
                    lp = self.lead(mz, tp, vel, st["speed"])
                    ga = self.aim_at(mz, lp)
                    gt = tol * 1.6 + 0.012
                    if abs(wrap(cur[0] - ga[0])) < gt and abs(cur[1] - ga[1]) < gt:
                        fire[i] = True
            elif k == "laser":
                full = st.get("charge", 0.75)
                chg = w.get("charge", 0)
                if los and dist < st["range"]:
                    if w["cooldown"] <= 0.05:
                        if chg < full * 0.97:
                            fire[i] = True  # keep charging
                        else:
                            # fully charged: release when on target
                            on = yaw_err < tol and pitch_err < tol + 0.01
                            fire[i] = not on
                elif chg > 0:
                    fire[i] = chg < full + 1.6  # wait for line of sight
            elif k == "lightning":
                cone = math.radians(st.get("cone_deg", 30)) * 0.45
                if los and dist < st["range"] * 0.95 and w["cooldown"] <= 0.1 and me["energy"] > st.get("energy", 3) + 6:
                    if abs(wrap(cur[0] - math.atan2(tp[1] - mz[1], tp[0] - mz[0]))) < cone:
                        fire[i] = True
            elif k == "mortar":
                if primary == i and w["cooldown"] <= 0.05 and yaw_err < 0.03 and pitch_err < 0.03 and dist > 22:
                    fire[i] = True
                elif primary != i and w["cooldown"] <= 0.05 and 25 < dist < 230 and tg.get("kind") == "mech":
                    src = (me["pos"][0], me["pos"][1], me["pos"][2] + 9.5)
                    sol = self.mortar_solution(src, (tg["pos"][0], tg["pos"][1], tg["pos"][2] + 1), vel, st["speed"], st["gravity"], high=False)
                    if sol and abs(wrap(cur[0] - sol[0])) < 0.03 and abs(cur[1] - sol[1]) < 0.03:
                        fire[i] = True
        return aim, fire

    def weapon_value(self, kind, dist, los, w):
        if kind == "laser":
            ok = w["cooldown"] < 0.3 or w.get("charge", 0) > 0
            return 5 if (los and ok and dist < w["stats"]["range"]) else 0
        if kind == "lightning":
            return 6 if (los and dist < w["stats"]["range"] * 0.95) else 0
        if kind == "gun":
            ok = w["ammo"] > 0 and w["reload"] == 0
            return (4 if ok else 2) if (los and dist < w["stats"]["range"]) else 0
        if kind == "mortar":
            ok = w["cooldown"] < 0.5
            return (3 if los else 4.5) if (ok and 25 < dist < 230) else 0.5
        return 0


if __name__ == "__main__":
    mf.run(Bot)
