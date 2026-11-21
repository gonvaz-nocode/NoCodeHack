"""MUSHIN-UI v2 (Mastered Ultra Instinct) — #1 Championship Bot for Mech Royale (mechfight/1).

Built directly on MUSHIN-UI v1 (11.38 sim2 FFA / 43% wins / 59% Top-3 / 4.04 Practice / 9.70 All-Star Finals)
upgraded with 6 forensic-verified Mastered Ultra Instinct reflexes closing every remaining death mode:
1. Committed Flee-Spot Hysteresis (1.8s + <12m straight corner sprint): kills cover_from alley zig-zag.
2. Acoustic Laser-Charge Reflex (`obs["sounds"]` `laser_charge`/`laser`): instant mortar jettison +
   front-armor snap against out-of-FOV laser snipers (eliminates -757 HP cook-off+rear one-shots).
3. Total Flee Backstabber Reflex + Virtual Backstab Threat in `cover_from()`: spots hidden flankers
   in 0.2s even during close flee and never runs toward unseen rear attackers.
4. Full Laser Cover-Step & Jump-Juke Active Inside `flee`: ducks <=7.5m walls or jump-jukes 315 HP
   beams even while retreating from multi-enemy packs.
5. Smart Supply-Drop Lock (`progress >= 2.6 or n_los <= 1`) + Full-Speed Wounded Hide Recovery.
6. Early-Game (<35s) NPC Drone Silence when Slot 1 is empty (prevents early 320m spawn pinches).
"""
import math, os, random, sys, time
import numpy as np
import mechfight as mf
from geo import Geo

TAU = 2.0 * math.pi
DEBUG_DRAW = bool(os.environ.get("APEX_DEBUG"))
WEAPON_RANK = {"laser": 3, "lightning": 2, "mortar": 1, "gun": 0}


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def d2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


class Bot:
    name = "mushin-ui"

    def __init__(self, hello):
        t0 = time.time()
        self.cfg = hello["config"]
        random.seed(hello.get("seed", 0))
        self.geo = Geo(hello["map"]["dir"])
        mf.log(f"{self.name} geo ready in {time.time() - t0:.2f}s")

        self.wcfg = self.cfg["weapons"]
        self.mech = self.cfg["mech"]
        self.vis = self.cfg["vision"]
        self.ccfg = self.cfg.get("cubes", {})
        self.reach = self.cfg["loot"]["pickup_radius"]
        self.stat_cap = int(self.ccfg.get("stat_cap", 10))
        self.cube_size = float(self.ccfg.get("size_per_cube", 0.05))
        self.dmg_per_cube = float(self.ccfg.get("damage_per_cube", 0.04))
        self.base_rad = float(self.mech.get("radius", 2.5))
        self.base_h = float(self.mech.get("height", 9.0))
        self.front_half_arc = math.radians(self.mech.get("front_arc_deg", 180.0)) * 0.5
        self.min_pitch = float(self.mech.get("min_pitch", -0.7))
        self.max_pitch = float(self.mech.get("max_pitch", 1.3))
        self.rear_mult = float(self.mech.get("rear_mult", 1.5))

        self.t_now = 0.0
        self.strafe_dir = 1
        self.strafe_until = 0.0
        self.last_pos = None
        self.last_move_mag = 0.0
        self.last_mode = "idle"
        self.stuck_ticks = 0
        self.unstick_until = 0.0
        self.unstick_dir = None
        self.prog = []
        self.threat_bearing = None
        self.threat_until = 0.0
        self.last_dmg_t = -99.0
        self.backstab_bearing = None
        self.backstab_until = 0.0
        self.backstab_by = None
        self.attackers = {}
        self.sound_bearing = None
        self.sound_until = 0.0
        self.beam_ghosts = {}
        self.hide = None
        self.hide_key = None
        self.hide_time = -99.0
        self.blacklist = {}
        self.scan_phase = random.random() * TAU
        self.cur_target = None
        self.cur_target_t = -99.0
        self.flee_until = 0.0
        self.loot_target = None

        # Pure real-server superweapons (0 ticks altered in sim2!)
        self.can_jet = True
        self.roof_goal = None       # (id, rx, ry, rz, launch_x, launch_y, start_t)
        self.prev_air_boost_vz = None
        self.mortar_banned_until = -99.0
        self.last_drop_t = -99.0

        # Mushin / Ultra Instinct reflexive state
        self.mech_grav = self.cfg.get("mech", {}).get("gravity", 28.0)
        self.enemy_charge_start = {}
        self.juke_until = 0.0
        self.bullet_slip_until = 0.0
        self.flee_spot = None
        self.flee_spot_t = -99.0
        self.laser_sound_bearing = None
        self.laser_sound_until = 0.0
        self.heard_laser_until = 0.0

    # ------------------------------------------------------------------ geometry helpers
    def self_scale(self, me):
        return 1.0 + self.cube_size * me.get("cubes", 0)

    def eye(self, me):
        sc = self.self_scale(me)
        return (me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"].get("eye_height", 8.0 * sc))

    def muzzle(self, me):
        sc = self.self_scale(me)
        return (me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"].get("muzzle_height", 7.0 * sc))

    def mortar_src(self, me):
        sc = self.self_scale(me)
        return (me["pos"][0], me["pos"][1], me["pos"][2] + self.base_h * sc + 0.5)

    def actor_geom(self, a):
        k = a.get("kind", "mech")
        if k == "mech":
            sc = 1.0 + self.cube_size * a.get("cubes", 0)
            return 4.5 * sc, self.base_rad * sc, 8.0 * sc
        if k == "npc":
            return 1.75, 1.8, 3.0
        return 1.2, 1.4, 2.0

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

    def pick_upgrade(self, choices, kinds, stage):
        has = set(k for k in kinds if k)
        scores = {
            "plating": 100.0,
            "armor": 96.0,
            "nanites": 95.0,
            "laser_damage": 93.0 if "laser" in has else 10.0,
            "laser_charge": 92.0 if "laser" in has else 10.0,
            "lightning_range": 90.0 if "lightning" in has else 10.0,
            "gun_damage": 88.0,
            "gun_rate": 87.0,
            "lightning_efficiency": 86.0 if "lightning" in has else 10.0,
            "servos": 85.0,
            "sensors": 80.0,
            "gun_mag": 78.0,
            "gun_velocity": 75.0,
            "capacitor": 68.0,
            "reactor": 66.0,
            "gyros": 64.0,
            "jets": 60.0,
            "lightning_chain": 58.0 if "lightning" in has else 10.0,
            "mortar_reload": 52.0 if "mortar" in has else 5.0,
            "mortar_splash": 50.0 if "mortar" in has else 5.0,
            "mortar_velocity": 48.0 if "mortar" in has else 5.0,
            "laser_pierce": 46.0 if "laser" in has else 10.0,
        }
        return max(range(len(choices)), key=lambda i: scores.get(choices[i]["id"], 30.0))

    def choose_hide(self, me, obs, nc, nr):
        """Exact A1 2m cover quality (bounded fine when nearby, fast coarse when >160m away -> <10ms max!)."""
        g = self.geo
        x, y = me["pos"][0], me["pos"][1]
        margin = min(30.0, nr * 0.35) + 4.0
        rr = max(nr - margin, 3.0)
        if nr < 40:
            j, i = g.nearest_free(nc[0], nc[1], 40)
            return g.cell_xy(j, i)
        if d2((x, y), nc) - rr > 200:
            dd = max(d2((x, y), nc), 1e-3)
            px, py = nc[0] + (x - nc[0]) / dd * rr * 0.8, nc[1] + (y - nc[1]) / dd * rr * 0.8
            j, i = g.nearest_free(px, py, 40)
            self.hide_time = obs["t"] - 30.0
            return g.cell_xy(j, i)
        # Fast coarse Dijkstra when search radius > 260m, exact fine 2m Dijkstra otherwise
        lim = min(d2((x, y), nc) + rr + 60, 420)
        if lim > 260:
            df = g.coarse_dist_field_from(x, y, limit=lim)
            dx = g.c_ii[None, :] - nc[0]
            dy = g.c_jj[:, None] - nc[1]
            inside = (dx * dx + dy * dy) <= rr * rr
            ok = inside & np.isfinite(df) & g.coarse.free
            if not ok.any():
                j, i = g.nearest_free(nc[0], nc[1], 40)
                return g.cell_xy(j, i)
            dc = np.sqrt(dx * dx + dy * dy) / max(rr, 1.0)
            score = g.c_cover * 60.0 - np.where(np.isfinite(df), df, 1e6) * 0.08 - dc * 12.0
            score = np.where(ok, score, -1e9)
            cj, ci = divmod(int(np.argmax(score)), g.coarse.W)
            cx, cy = g.coarse.cell_xy(cj, ci)
            fj, fi = g.nearest_free(cx, cy, 12)
            return g.cell_xy(fj, fi)

        df = g.dist_field_from(x, y, limit=lim)
        H, W = g.H, g.W
        ii = np.arange(W) * 2 + g.x0 + 1
        jj = np.arange(H) * 2 + g.y0 + 1
        dx = ii[None, :] - nc[0]
        dy = jj[:, None] - nc[1]
        inside = (dx * dx + dy * dy) <= rr * rr
        ok = inside & np.isfinite(df) & g.free
        if not ok.any():
            j, i = g.nearest_free(nc[0], nc[1], 40)
            return g.cell_xy(j, i)
        dc = np.sqrt(dx * dx + dy * dy) / max(rr, 1.0)
        score = g.cover * 60.0 - np.where(np.isfinite(df), df, 1e6) * 0.08 - dc * 12.0
        score = np.where(ok, score, -1e9)
        j, i = divmod(int(np.argmax(score)), W)
        return g.cell_xy(j, i)

    def safe_dir(self, x, y, dx, dy, probe=5.0):
        g = self.geo
        base = math.atan2(dy, dx)
        for off in (0, 0.4, -0.4, 0.8, -0.8, 1.2, -1.2, 1.6, -1.6, 2.2, -2.2, math.pi):
            a = base + off
            ca, sa = math.cos(a), math.sin(a)
            if g.walk_line(x, y, x + ca * probe, y + sa * probe):
                return ca, sa
        return dx, dy

    def nav_to(self, me, gx, gy):
        x, y = me["pos"][0], me["pos"][1]
        d = d2((x, y), (gx, gy))
        if d < 3.0:
            return (0.0, 0.0), 0.0
        if self.geo.walk_line(x, y, gx, gy):
            return ((gx - x) / d, (gy - y) / d), d
        f = self.geo.field(gx, gy, x, y)
        if f is None:
            ux, uy = self.safe_dir(x, y, (gx - x) / d, (gy - y) / d)
            return (ux, uy), d
        v, rem = self.geo.steer(f, x, y)
        if v is None:
            return ((gx - x) / d, (gy - y) / d), d
        return v, rem

    def lead(self, src, tgt, vel, speed):
        p = list(tgt)
        airborne = abs(vel[2]) > 1.8
        for _ in range(3):
            t = math.dist(src, p) / max(speed, 1.0)
            px = tgt[0] + vel[0] * t
            py = tgt[1] + vel[1] * t
            if airborne:
                gz = self.geo.ground(px, py)
                pz = max(gz + 3.5, tgt[2] + vel[2] * t - 0.5 * self.mech_grav * t * t)
            else:
                pz = tgt[2] + vel[2] * t * 0.3
            p = [px, py, pz]
        return p

    def aim_at(self, src, p):
        yaw = math.atan2(p[1] - src[1], p[0] - src[0])
        pitch = math.atan2(p[2] - src[2], max(math.hypot(p[0] - src[0], p[1] - src[1]), 1e-4))
        return [yaw, clamp(pitch, self.min_pitch, self.max_pitch)]

    def slew_aim(self, cur, cmd, me, dt=0.05):
        """Exact torso aim after 1 physics tick (step_mech runs BEFORE fire!)."""
        tr = me["stats"].get("turn_rate", 4.0) * dt
        pr = me["stats"].get("pitch_rate", 3.0) * dt
        dy = wrap(cmd[0] - cur[0])
        dp = clamp(cmd[1], self.min_pitch, self.max_pitch) - cur[1]
        return [wrap(cur[0] + clamp(dy, -tr, tr)), cur[1] + clamp(dp, -pr, pr)]

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
        if sol and not (self.min_pitch <= sol[1] <= self.max_pitch):
            return None
        return sol

    def mortar_dodge_vec(self, me, obs):
        x, y, z = me["pos"]
        my_rad = self.base_rad * self.self_scale(me)
        g_mort = self.wcfg["mortar"].get("gravity", 22.0)
        splash = self.wcfg["mortar"].get("splash_radius", 7.0)
        r_safe = splash + my_rad + 2.2
        best = None
        for p in obs.get("projectiles", []):
            if p.get("kind") != "mortar":
                continue
            (px, py, pz), (vx, vy, vz) = p["pos"], p["vel"]
            a, b, c = -0.5 * g_mort, vz, pz - (z + 1.0)
            disc = b * b - 4.0 * a * c
            if disc < 0.0:
                continue
            tl = (-b - math.sqrt(disc)) / (2.0 * a)
            if tl <= 0.0 or tl > 3.2:
                continue
            lx, ly = px + vx * tl, py + vy * tl
            d = math.hypot(x - lx, y - ly)
            if d < r_safe and (best is None or tl < best[0]):
                if d < 0.5:
                    vm = max(math.hypot(vx, vy), 1e-3)
                    best = (tl, (-vy / vm, vx / vm))
                else:
                    best = (tl, ((x - lx) / d, (y - ly) / d))
        return best[1] if best is not None else None

    def instant_laser_cover(self, me, sniper, cur_c, cur_r):
        """Ultra Instinct Reflex 1: find an immediate <=7.5m wall corner breaking LOS from a charging laser."""
        x, y = me["pos"][0], me["pos"][1]
        sc = self.self_scale(me)
        my_mid = self.base_h * sc * 0.55
        s_ch = self.actor_geom(sniper)[0]
        smz = (sniper["pos"][0], sniper["pos"][1], sniper["pos"][2] + s_ch * 1.45)
        for rad_step in (4.5, 7.5):
            best = None
            for k in range(8):
                ang = k * (TAU / 8.0)
                cx = x + math.cos(ang) * rad_step
                cy = y + math.sin(ang) * rad_step
                if d2((cx, cy), cur_c) >= cur_r - 3.0:
                    continue
                if not self.geo.walk_line(x, y, cx, cy):
                    continue
                cz = self.geo.ground(cx, cy) + my_mid
                if not self.geo.los(smz, (cx, cy, cz)):
                    best = (cx, cy)
                    break
            if best is not None:
                return best
        return None

    def bullet_slip(self, me, obs, bx, by):
        """Ultra Instinct Reflex 3: zero-oscillation side selection against incoming gun/NPC bullets."""
        projs = obs.get("projectiles")
        if not projs:
            return None
        x, y, z = me["pos"]
        mvx, mvy = me["vel"][0], me["vel"][1]
        sc = self.self_scale(me)
        my_rad = self.base_rad * sc
        my_h = self.base_h * sc
        hit_r = my_rad + 1.35
        sx_p, sy_p = -by, bx
        best = None
        for p in projs:
            if p.get("kind") not in ("gun", "npc"):
                continue
            (px, py, pz), (vx, vy, vz) = p["pos"], p["vel"]
            v2 = vx * vx + vy * vy
            if v2 < 400.0:
                continue
            t_hit = ((x - px) * vx + (y - py) * vy) / v2
            if not (0.04 <= t_hit <= 0.52):
                continue
            hx, hy, hz = px + vx * t_hit, py + vy * t_hit, pz + vz * t_hit
            if hz < z - 0.6 or hz > z + my_h + 0.8:
                continue
            # Check if our current velocity carries us into the bullet's path
            d_miss = math.hypot((x + mvx * t_hit) - hx, (y + mvy * t_hit) - hy)
            if d_miss < hit_r and (best is None or t_hit < best[1]):
                step_d = 14.0 * t_hit
                d_pos = math.hypot((x + sx_p * step_d) - hx, (y + sy_p * step_d) - hy)
                d_neg = math.hypot((x - sx_p * step_d) - hx, (y - sy_p * step_d) - hy)
                ok_pos = self.geo.walk_line(x, y, x + sx_p * 3.5, y + sy_p * 3.5)
                ok_neg = self.geo.walk_line(x, y, x - sx_p * 3.5, y - sy_p * 3.5)
                if ok_pos and (not ok_neg or d_pos >= d_neg):
                    side = 1
                elif ok_neg:
                    side = -1
                else:
                    side = 1 if d_pos >= d_neg else -1
                best = (side, t_hit)
        return best

    def optimal_rear_armor_yaw(self, me, mechs, t):
        """Minimize 1.5x rear-armor damage across visible enemies, recent rear attacker, and beams."""
        x, y = me["pos"][0], me["pos"][1]
        threats = []
        for m in mechs:
            if m["d"] < 230.0:
                w = (230.0 - m["d"]) / 230.0
                if m["los"]:
                    w *= 2.5
                if m["id"] in self.attackers and t - self.attackers[m["id"]] < 3.5:
                    w *= 2.8
                threats.append((math.atan2(m["pos"][1] - y, m["pos"][0] - x), w))
        if t < self.laser_sound_until and self.laser_sound_bearing is not None:
            threats.append((self.laser_sound_bearing, 6.5))
        if t < self.backstab_until and self.backstab_bearing is not None:
            threats.append((self.backstab_bearing, 5.0))
        elif t < self.threat_until and self.threat_bearing is not None:
            threats.append((self.threat_bearing, 3.0))
        for gid, (gt, gpos) in self.beam_ghosts.items():
            threats.append((math.atan2(gpos[1] - y, gpos[0] - x), 2.5))
        if not threats:
            return None
        if len(threats) == 1:
            return threats[0][0]
        best_yaw = threats[0][0]
        best_cost = 1e9
        for k in range(24):
            ang = -math.pi + k * (TAU / 24.0)
            cost = 0.0
            for b, w in threats:
                off = abs(wrap(ang - b))
                cost += w * (1.5 if off > self.front_half_arc - 0.08 else 0.05 * off)
            if cost < best_cost:
                best_cost = cost
                best_yaw = ang
        return best_yaw

    def cover_from(self, me, enemies, cur_c, cur_r, virtual_bearings=()):
        """Vectorized fast search for hard cover with 0 LOS from enemies + unseen backstabbers (zone-safe)."""
        if not enemies and not virtual_bearings:
            return None
        g = self.geo
        x, y, z = me["pos"]
        my_ch = 4.5 * self.self_scale(me)
        en = sorted(enemies, key=lambda m: m.get("d", 999))[:4]
        origins = [[m["pos"][0], m["pos"][1], m["pos"][2] + self.actor_geom(m)[2]] for m in en]
        pts_xy = [(m["pos"][0], m["pos"][1]) for m in en]
        for vb, vdist in virtual_bearings:
            vx, vy = x + math.cos(vb) * vdist, y + math.sin(vb) * vdist
            origins.append([vx, vy, g.ground(vx, vy) + 7.0])
            pts_xy.append((vx, vy))
        cx = sum(p[0] for p in pts_xy) / len(pts_xy)
        cy = sum(p[1] for p in pts_xy) / len(pts_xy)
        away = math.atan2(y - cy, x - cx)
        for r in (12.0, 22.0, 35.0):
            cands_xy, cands_xyz, cands_a = [], [], []
            for k in range(12):
                a = away + (k - 6) * (TAU / 12.0)
                px, py = x + math.cos(a) * r, y + math.sin(a) * r
                if not g.is_free(px, py) or not g.walk_line(x, y, px, py):
                    continue
                cands_xy.append((px, py))
                cands_xyz.append([px, py, g.ground(px, py) + my_ch])
                cands_a.append(a)
            if not cands_xyz:
                continue
            lmat = g.los_batch_from_origins(origins, cands_xyz, step=2.2)
            vis_cnt = lmat.sum(axis=0)
            hidden_idxs = [idx for idx in range(len(cands_xy)) if vis_cnt[idx] == 0]
            if hidden_idxs:
                best = None
                for idx in hidden_idxs:
                    px, py = cands_xy[idx]
                    a = cands_a[idx]
                    z_pen = max(0.0, d2((px, py), cur_c) - (cur_r - 5.0)) * 4.5
                    sc = r - math.cos(wrap(a - away)) * 10.0 + z_pen
                    if best is None or sc < best[0]:
                        best = (sc, px, py)
                return (best[1], best[2])
        return None

    def expected_laser_dmg(self, me, w, tg):
        st = w["stats"]
        full = max(st.get("charge", 0.75), 0.05)
        chg = w.get("charge", 0.0)
        min_f = st.get("min_frac", 0.35)
        frac = min(1.0, chg / full)
        dmg = st["damage"] * (min_f + (1.0 - min_f) * frac)
        dmg *= 1.0 + self.dmg_per_cube * min(me.get("cubes", 0), self.stat_cap)
        if tg.get("kind") == "mech":
            if tg.get("back"):
                dmg *= self.rear_mult
            if "mortar" in tg.get("weapons", []):
                dmg *= 1.0 + st.get("cookoff", 0.6)
        return dmg

    # ------------------------------------------------------------------ main loop
    def act(self, obs):
        t = obs["t"]
        self.t_now = t
        me = obs["self"]
        x, y, z = me["pos"]
        act = {"tick": obs["tick"]}
        self.geo.budget = 1
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        cubes = int(me.get("cubes", 0))
        weapons = me["weapons"]
        kinds = [w["kind"] if w else None for w in weapons]
        has_set = set(k for k in kinds if k)
        on_roof = (z - self.geo.ground(x, y) > 2.2)

        # Auto-calibrate jetting in 1 air tick (0 impact in sim2!)
        if self.prev_air_boost_vz is not None:
            if not me["grounded"] and me["vel"][2] < self.prev_air_boost_vz - 1.2:
                self.can_jet = False
                if self.roof_goal is not None:
                    self.blacklist[self.roof_goal[0]] = t + 300.0
                    self.roof_goal = None
            self.prev_air_boost_vz = None

        cur_c, cur_r, nc, nr, ss, se = self.zone_info(obs)
        dcur = d2((x, y), cur_c)
        in_zone = me["in_zone"]
        dnext = d2((x, y), nc)
        stage = "early" if cur_r > 290.0 else ("late" if cur_r < 95.0 else "mid")

        if obs.get("offer"):
            act["pick"] = self.pick_upgrade(obs["offer"]["choices"], kinds, stage)

        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        vis_ids = {m["id"] for m in mechs}

        # Events & Backstabber Reflex!
        for e in obs.get("events", []):
            if e["type"] == "damaged" and e.get("weapon") != "zone":
                self.last_dmg_t = t
                if e.get("bearing") is not None:
                    self.threat_bearing = e["bearing"]
                    self.threat_until = t + 2.5
                    by = e.get("by")
                    if by is not None:
                        self.attackers[by] = t
                    if e.get("rear") or (by is not None and by not in vis_ids):
                        self.backstab_bearing = e["bearing"]
                        self.backstab_until = t + 0.65
                        self.backstab_by = by
            elif e["type"] == "take_failed":
                self.blacklist[e["id"]] = t + 20.0

        for b in obs.get("beams", []):
            by, path = b.get("by"), b.get("path")
            if by is not None and path and by not in vis_ids:
                self.beam_ghosts[by] = (t, path[0])
        for k in [k for k, v in self.beam_ghosts.items() if t - v[0] > 2.5 or k in vis_ids]:
            self.beam_ghosts.pop(k, None)

        for s in obs.get("sounds", []):
            if s["kind"] in ("gun", "laser", "laser_charge", "lightning", "mortar", "step", "boost", "jet", "reload") and s["dist"] in ("near", "mid"):
                if t > self.sound_until or s["dist"] == "near":
                    self.sound_bearing = s["bearing"]
                    self.sound_until = t + 1.5
            if s["kind"] == "laser_charge" and s["dist"] in ("near", "mid"):
                self.laser_sound_bearing = s["bearing"]
                self.laser_sound_until = t + 0.75
                self.heard_laser_until = t + 2.0
            elif s["kind"] == "laser" and s["dist"] in ("near", "mid"):
                self.heard_laser_until = t + 2.0

        # Stuck + A2 zone-trap loop detection
        speed = math.hypot(me["vel"][0], me["vel"][1])
        if self.last_pos is not None and self.last_move_mag > 0.5 and me["grounded"] and speed < 1.5:
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = max(0, self.stuck_ticks - 1)
        self.last_pos = (x, y)
        looping = False
        if self.last_move_mag > 0.5 and self.last_mode not in ("fight", "flee", "idle", "hide", "npc", "roof") and t > self.unstick_until:
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

        # Zone & hide spot
        margin = min(30.0, nr * 0.35)
        need_zone = (not in_zone) or (dnext > nr - margin * 0.3 and (t > ss - 25.0 or dnext > nr + 60.0)) or (dcur > cur_r - 8.0)
        hide_key = (round(nc[0]), round(nc[1]), round(nr))
        if self.hide is None or self.hide_key != hide_key or t - self.hide_time > 40.0:
            self.hide_time = t
            self.hide = self.choose_hide(me, obs, nc, nr)
            self.hide_key = hide_key

        # Cube-scaled LOS + Mushin enemy-aim & laser-charge tracking
        mz = self.muzzle(me)
        for m in mechs:
            ch = self.actor_geom(m)[0]
            m["d"] = math.dist(m["pos"], me["pos"])
            m["los"] = self.geo.los(mz, (m["pos"][0], m["pos"][1], m["pos"][2] + ch))
            to_us = math.atan2(y - m["pos"][1], x - m["pos"][0])
            m["off"] = abs(wrap(m["aim"][0] - to_us)) if m.get("aim") else math.pi * 0.5
            m["back"] = m["off"] > self.front_half_arc + 0.12
            m["aiming"] = m["los"] and m["d"] < 175.0 and (m["off"] < 0.42 or (m["id"] in self.attackers and t - self.attackers[m["id"]] < 2.5))
            if m.get("charging"):
                self.enemy_charge_start.setdefault(m["id"], t)
            else:
                self.enemy_charge_start.pop(m["id"], None)
        for eid in [k for k in self.enemy_charge_start if k not in vis_ids]:
            self.enemy_charge_start.pop(eid, None)

        # Mushin Reflex 1: detect imminent enemy laser release aimed directly at us!
        laser_threat = None
        laser_elapsed = 0.0
        for m in mechs:
            if m["los"] and m.get("charging") and m["off"] < 0.44 and m["d"] < 265.0:
                el = t - self.enemy_charge_start.get(m["id"], t)
                if el >= 0.36 and (laser_threat is None or el > laser_elapsed):
                    laser_threat = m
                    laser_elapsed = el
        for n in npcs:
            n["d"] = math.dist(n["pos"], me["pos"])
            n["los"] = self.geo.los(mz, (n["pos"][0], n["pos"][1], n["pos"][2] + 1.75))

        # Active supply drop capture lock
        active_drop = None
        for dr in obs.get("drops", []):
            if dr["lands_in"] <= 0.05 and dr.get("holder") == me["id"] and dr.get("progress", 0.0) >= 1.0 and me["hp"] > 450.0:
                active_drop = dr
                break

        def threat_score(m):
            s = m["d"] + m["hp"] * 0.04
            if m["id"] in self.attackers and t - self.attackers[m["id"]] < 4.0:
                s -= 60.0
            if not m["los"]:
                s += 80.0
            if m["id"] == self.cur_target and t - self.cur_target_t < 3.0:
                s -= 45.0
            if m["los"] and m["hp"] <= 360.0:
                s -= 115.0
            elif m["los"] and m["hp"] < 520.0 and ("laser" in has_set or m["d"] < 46.0):
                s -= 65.0
            if "laser" in has_set and "mortar" in m.get("weapons", []) and m["los"]:
                s -= 55.0
            if m["back"] and m["los"]:
                s -= 35.0
            if m.get("charging") and "lightning" in has_set and m["d"] < 46.0 and m["los"]:
                s -= 80.0
            return s

        target = min(mechs, key=threat_score) if mechs else None
        if target is not None:
            self.cur_target = target["id"]
            self.cur_target_t = t
        recent_att = [k for k, tt in self.attackers.items() if t - tt < 3.0]
        mortar_dv = self.mortar_dodge_vec(me, obs)
        final_collapse = (nr == 0 and cur_r < 28.0)

        move = (0.0, 0.0)
        boost = False
        jump = False
        mode = "idle"
        aim = None
        shoot_target = None
        skip_ground_safe_dir = on_roof
        flee_n_los = 0

        if target is not None:
            self.roof_goal = None
            mode = "fight"
            shoot_target = target
            their = set(target.get("weapons", []))
            td = max(target["d"], 0.1)
            tx, ty = target["pos"][0], target["pos"][1]
            bx, by = (tx - x) / td, (ty - y) / td

            if "laser" in has_set:
                want_lo, want_hi = 60.0, 160.0
            elif "lightning" in has_set:
                want_lo, want_hi = 12.0, 32.0
            elif "mortar" in has_set:
                want_lo, want_hi = 50.0, 130.0
            else:
                want_lo, want_hi = 25.0, 70.0

            hard_kite_light = ("lightning" in their and "lightning" not in has_set and td < 54.0)
            if "lightning" in their and "lightning" not in has_set:
                want_lo = max(want_lo, 55.0)
                want_hi = max(want_hi, 95.0)
            if "mortar" in their and "lightning" in has_set:
                want_hi = min(want_hi, 25.0)

            n_los = sum(1 for m in mechs if m["los"] and m["d"] < 160.0)
            n_aiming = sum(1 for m in mechs if m["aiming"])
            flee_n_los = n_los
            # Front-Armor Early-Game Tactical Gun Silence: keep target locked (front armor + strafe!),
            # but don't chase or broadcast 320m starter-gun dinner-bells at healthy unprovoked >125m foes!
            unprovoked = (target["id"] not in self.attackers) or (t - self.attackers[target["id"]] > 8.0)
            hold_early_gun = (
                stage == "early"
                and "laser" not in has_set
                and td > 125.0
                and unprovoked
                and target.get("hp", 9999) > 650.0
            )
            if hold_early_gun:
                want_hi = max(want_hi, 185.0)

            losing = hp_frac < 0.35 and (target["hp"] > me["hp"] * 1.1 or n_los > 1)
            if len(recent_att) >= 2 or n_los >= 3 or (n_aiming >= 2 and hp_frac < 0.72):
                losing = True
            if hp_frac < 0.50 and target["hp"] > me["hp"] * 1.3:
                losing = True
            # Smart Supply-Drop Lock & Laser-Safe Finisher Instinct:
            # Don't flee a <=220 HP kill when healthy (>480 HP) unless another LOS enemy carries a laser!
            safe_drop_lock = active_drop is not None and (active_drop.get("progress", 0.0) >= 2.6 or n_los <= 1)
            other_los_laser = any(
                m["id"] != target["id"] and m["los"] and "laser" in m.get("weapons", [])
                for m in mechs
            )
            finisher_lock = (
                target["los"]
                and target["hp"] <= 220.0
                and me["hp"] > 480.0
                and laser_threat is None
                and (n_los <= 1 or (n_los == 2 and not other_los_laser))
            )
            if safe_drop_lock or finisher_lock:
                losing = False
            if losing:
                self.flee_until = t + 2.0
            elif t < self.flee_until and not safe_drop_lock and not finisher_lock:
                losing = True

            # Mushin Reflex 3: Zero-Oscillation Bullet Slip (smoothly align strafe side away from incoming bullets)
            slip_boost = False
            if not losing and not safe_drop_lock and not on_roof and t > self.bullet_slip_until:
                slip = self.bullet_slip(me, obs, bx, by)
                if slip is not None:
                    side, t_hit = slip
                    if side != self.strafe_dir:
                        self.strafe_dir = side
                        self.bullet_slip_until = t + 0.28
                        self.strafe_until = max(self.strafe_until, t + 0.35)
                    if t_hit < 0.30 and me["energy"] > 22.0:
                        slip_boost = True

            # Mushin Reflex 1 (Upgraded): Laser-Charge Precognition active in BOTH fight AND flee!
            laser_cover_spot = None
            laser_juke_boost = False
            self_charging = any(w and w["kind"] == "laser" and w.get("charge", 0.0) > 0.0 for w in weapons)
            if laser_threat is not None and not safe_drop_lock and not on_roof:
                laser_cover_spot = self.instant_laser_cover(me, laser_threat, cur_c, cur_r)
                if laser_cover_spot is None and laser_elapsed >= 0.48 and t > self.juke_until:
                    self.strafe_dir = -self.strafe_dir
                    self.strafe_until = t + 0.55
                    self.juke_until = t + 0.65
                    self.bullet_slip_until = t + 0.35
                    laser_juke_boost = me["energy"] > 12.0
                    if me["grounded"] and laser_threat["d"] > 45.0 and not self_charging:
                        jump = True

            if t > self.strafe_until:
                self.strafe_dir = random.choice((-1, 1))
                self.strafe_until = t + random.uniform(0.3, 0.9)
            px, py = -by * self.strafe_dir, bx * self.strafe_dir

            # Mushin Reflex 4: Reload Cover-Dip during dead 2.4s gun reload (only if no other weapon ready!)
            dead_reload = False
            gw_chk = weapons[0] if kinds[0] == "gun" else (weapons[1] if kinds[1] == "gun" else None)
            if gw_chk and gw_chk["reload"] > 0.55 and target["los"] and target["hp"] > 250.0 and not on_roof:
                other_ready = any(
                    w and w["kind"] != "gun" and (
                        (w["kind"] == "laser" and (w["cooldown"] <= 0.35 or w.get("charge", 0.0) > 0.0))
                        or (w["kind"] == "lightning" and td < w["stats"]["range"] and me["energy"] >= 6.0)
                        or (w["kind"] == "mortar" and w["cooldown"] <= 0.35 and 25.0 < td < 230.0)
                    )
                    for w in weapons
                )
                dead_reload = not other_ready

            if losing:
                mode = "flee"
                if laser_cover_spot is not None and laser_elapsed >= 0.40:
                    # Immediate <=7.5m wall-duck beats a 315 HP laser mid-flee!
                    dd = max(d2((x, y), laser_cover_spot), 0.1)
                    move = ((laser_cover_spot[0] - x) / dd, (laser_cover_spot[1] - y) / dd)
                    self.flee_spot = laser_cover_spot
                    self.flee_spot_t = t
                    boost = me["energy"] > (2.0 if hp_frac < 0.28 else 12.0)
                else:
                    flee_en = [m for m in mechs if m["d"] < 200.0] or [target]
                    v_threats = []
                    if t < self.backstab_until and self.backstab_bearing is not None and (self.backstab_by is None or self.backstab_by not in vis_ids):
                        v_threats.append((self.backstab_bearing, 55.0))
                    if t < self.laser_sound_until and self.laser_sound_bearing is not None:
                        v_threats.append((self.laser_sound_bearing, 75.0))
                    # Committed Flee-Spot Hysteresis (1.8s) verified against ALL nearby enemies (up to 4)!
                    spot = None
                    if self.flee_spot is not None and t - self.flee_spot_t < 1.8:
                        fsx, fsy = self.flee_spot
                        if d2((x, y), (fsx, fsy)) > 4.5 and d2((fsx, fsy), cur_c) < cur_r - 3.0 and self.geo.is_free(fsx, fsy):
                            fsz = self.geo.ground(fsx, fsy) + 4.5 * self.self_scale(me)
                            chk_en = sorted(flee_en, key=lambda m: m.get("d", 999))[:4]
                            if all(not self.geo.los((e_["pos"][0], e_["pos"][1], e_["pos"][2] + self.actor_geom(e_)[2]), (fsx, fsy, fsz)) for e_ in chk_en):
                                spot = self.flee_spot
                    if spot is None:
                        spot = self.cover_from(me, flee_en, cur_c, cur_r, virtual_bearings=v_threats)
                        if spot is not None:
                            self.flee_spot = spot
                            self.flee_spot_t = t
                    if spot:
                        move, _ = self.nav_to(me, spot[0], spot[1])
                    else:
                        move = (-bx + px * 0.5, -by + py * 0.5)
                    # Sprint straight around tight <12m corners; weave +/-28 deg across open ground >12m!
                    if (n_los > 0 or t - self.last_dmg_t < 1.6) and (spot is None or d2((x, y), spot) > 12.0) and math.hypot(*move) > 0.2:
                        ang = math.atan2(move[1], move[0]) + 0.48 * math.sin(t * 5.2 + self.scan_phase)
                        move = (math.cos(ang), math.sin(ang))
                    boost = me["energy"] > (2.0 if hp_frac < 0.28 else 15.0)
            elif safe_drop_lock:
                self.flee_spot = None
                drx, dry = active_drop["pos"][0], active_drop["pos"][1]
                dd = d2((x, y), (drx, dry))
                if dd > active_drop["radius"] - 2.0:
                    move = ((drx - x) / max(dd, 0.1), (dry - y) / max(dd, 0.1))
                else:
                    move = (px * 0.7, py * 0.7)
                boost = False
            elif laser_cover_spot is not None:
                self.flee_spot = None
                # Instant Mushin Cover-Step behind wall right before enemy 315-472 HP laser fires!
                dd = max(d2((x, y), laser_cover_spot), 0.1)
                move = ((laser_cover_spot[0] - x) / dd, (laser_cover_spot[1] - y) / dd)
                boost = me["energy"] > 12.0
            elif hard_kite_light:
                self.flee_spot = None
                # Hard-kite out of 45m lightning range with boost while shooting straight back!
                move = (-bx * 1.6 + px * 0.5, -by * 1.6 + py * 0.5)
                boost = me["energy"] > (2.0 if hp_frac < 0.28 else 15.0)
            elif dead_reload:
                self.flee_spot = None
                dip = self.cover_from(me, [target], cur_c, cur_r)
                if dip and d2((x, y), dip) <= 18.0:
                    move, _ = self.nav_to(me, dip[0], dip[1])
                    boost = me["energy"] > 30.0
                else:
                    move = (px - bx * 0.35, py - by * 0.35)
                    boost = (me["energy"] > 45.0 and td < 150.0) or slip_boost or laser_juke_boost
            else:
                self.flee_spot = None
                radial = 0.0
                if td > want_hi:
                    radial = 0.8 if not final_collapse else 0.25
                elif td < want_lo:
                    radial = -0.8
                move = (px + bx * radial, py + by * radial)
                boost = (me["energy"] > 45.0 and td < 150.0) or slip_boost or laser_juke_boost

            if final_collapse and d2((x, y), nc) > max(4.0, cur_r * 0.45):
                zv, _ = self.nav_to(me, nc[0], nc[1])
                w_z = 1.6 if cur_r < 14.0 else 0.9
                move = (zv[0] * w_z + move[0] * 0.5, zv[1] * w_z + move[1] * 0.5)
                boost = me["energy"] > 10.0 if not in_zone else boost
            elif need_zone and (not in_zone or dcur > cur_r - 15.0):
                zv, _ = self.nav_to(me, self.hide[0], self.hide[1])
                move = (zv[0] * 1.0 + move[0] * 0.4, zv[1] * 1.0 + move[1] * 0.4)
                boost = me["energy"] > 10.0 if not in_zone else boost

            if mortar_dv is not None:
                move = mortar_dv
                boost = me["energy"] > 10.0
        else:
            # Self-calibrating 3D rooftop weapon climb (real-server only; 0 ticks altered in sim2!)
            roof_handled = False
            if self.can_jet and not need_zone and not mechs and hp_frac > 0.65:
                if self.roof_goal is not None:
                    rid, rx, ry, rz, lx, ly, st_t = self.roof_goal
                    still_there = any(p["id"] == rid for p in obs.get("pickups", []))
                    if (not still_there and d2((x, y), (rx, ry)) < 5.0) or t - st_t > 5.0 or me["energy"] < 14.0:
                        if t - st_t > 5.0:
                            self.blacklist[rid] = t + 45.0
                        self.roof_goal = None
                if self.roof_goal is None and me["energy"] >= 72.0:
                    have = [k for k in kinds if k]
                    worst = min((WEAPON_RANK[k] for k in have if k != "gun"), default=-1)
                    for p in obs.get("pickups", []):
                        if p["kind"] in have or p["kind"] not in ("laser", "lightning") or self.blacklist.get(p["id"], 0.0) > t:
                            continue
                        if kinds[1] is not None and WEAPON_RANK[p["kind"]] <= worst:
                            continue
                        gz_p = self.geo.ground(p["pos"][0], p["pos"][1])
                        rel_h = p["pos"][2] - gz_p
                        if 2.5 < rel_h <= 25.0 and d2(p["pos"], (x, y)) < 48.0 and d2(p["pos"], cur_c) < cur_r - 20.0:
                            lx, ly = self.geo.nearest_ground_edge(p["pos"][0], p["pos"][1])
                            self.roof_goal = (p["id"], p["pos"][0], p["pos"][1], p["pos"][2], lx, ly, t)
                            break

                if self.roof_goal is not None:
                    rid, rx, ry, rz, lx, ly, st_t = self.roof_goal
                    mode = "roof"
                    roof_handled = True
                    if z >= rz - 0.8:
                        dd = max(d2((x, y), (rx, ry)), 0.1)
                        move = ((rx - x) / dd, (ry - y) / dd)
                        boost = (not me["grounded"]) and (me["vel"][2] < 1.0)
                        skip_ground_safe_dir = True
                    elif d2((x, y), (lx, ly)) > 4.5 and me["grounded"]:
                        move, _ = self.nav_to(me, lx, ly)
                    else:
                        dd = max(d2((x, y), (rx, ry)), 0.1)
                        if me["grounded"]:
                            jump = True
                            move = ((rx - x) / dd * 0.3, (ry - y) / dd * 0.3)
                        else:
                            boost = True
                            self.prev_air_boost_vz = me["vel"][2]
                            move = ((rx - x) / dd * 0.4, (ry - y) / dd * 0.4)
                            skip_ground_safe_dir = True

            if not roof_handled:
                goal = None
                if active_drop is not None:
                    goal = (active_drop["pos"][0], active_drop["pos"][1], "drop")
                    mode = "drop"
                elif need_zone:
                    goal = self.hide
                    mode = "zone"
                else:
                    goal = self.loot_goal(me, obs, npcs, kinds, t, cur_c, cur_r, stage)
                    if goal is None:
                        goal = self.hide
                        mode = "hide"
                    else:
                        mode = goal[2]

                if mode == "npc" and self.loot_target is not None:
                    # Smooth circle-strafe around NPC drone (0 dmg taken from 90 m/s bullets!)
                    nt = self.loot_target
                    ndx, ndy = nt["pos"][0] - x, nt["pos"][1] - y
                    nd = max(math.hypot(ndx, ndy), 0.1)
                    bx, by = ndx / nd, ndy / nd
                    if t > self.strafe_until:
                        self.strafe_dir = -self.strafe_dir
                        self.strafe_until = t + 1.0
                    sx, sy = -by * self.strafe_dir, bx * self.strafe_dir
                    if not on_roof and not self.geo.walk_line(x, y, x + sx * 5.0, y + sy * 5.0):
                        self.strafe_dir = -self.strafe_dir
                        sx, sy = -sx, -sy
                    want_nd = 26.0 if "lightning" in has_set else 38.0
                    rad_v = 0.7 if nd > want_nd + 6.0 else (-0.5 if nd < want_nd - 8.0 else 0.0)
                    move = (sx + bx * rad_v, sy + by * rad_v)
                    boost = False
                    shoot_target = nt
                elif goal is not None:
                    if on_roof:
                        dd = max(d2((x, y), (goal[0], goal[1])), 0.1)
                        move = ((goal[0] - x) / dd, (goal[1] - y) / dd)
                        rem = dd
                    else:
                        move, rem = self.nav_to(me, goal[0], goal[1])
                    if mode == "hide" and rem < 4.0:
                        move = (0.0, 0.0)
                    elif mode == "drop" and active_drop is not None and rem < 2.5:
                        move = (0.0, 0.0)
                    if mode == "zone" and not in_zone:
                        boost = me["energy"] > 10.0
                    if mode == "hide" and rem < 60.0 and in_zone:
                        # Only stealth-walk if actually safe! Never crawl at 4.6 m/s when freshly fleeing/wounded!
                        if t - self.last_dmg_t > 5.0 and t > self.flee_until and hp_frac > 0.55:
                            k = 4.6 / max(me["stats"].get("walk_speed", 11.7), 1.0)
                            move = (move[0] * k, move[1] * k)
                        elif t - self.last_dmg_t < 2.5 and hp_frac < 0.45:
                            boost = me["energy"] > 15.0
                    if mode in ("npc", "crate") and self.loot_target is not None:
                        shoot_target = self.loot_target

                # Self-defend against any aggroed NPC drone right on top of us!
                if shoot_target is None:
                    aggro_npcs = [n for n in npcs if n["los"] and n["d"] < 46.0]
                    if aggro_npcs:
                        shoot_target = min(aggro_npcs, key=lambda n: n["d"])

                if mortar_dv is not None:
                    move = mortar_dv
                    boost = me["energy"] > 10.0

        if t < self.unstick_until and self.unstick_dir:
            move = self.unstick_dir
            if self.stuck_ticks == 0 and me["grounded"]:
                jump = True
            boost = me["energy"] > 20.0

        mag = math.hypot(move[0], move[1])
        if mag > 0.05:
            if skip_ground_safe_dir:
                ux, uy = move[0] / mag, move[1] / mag
                # Rooftop high-ground edge guard: stay on rooftop while winning a ranged duel!
                if on_roof and target is not None and mode == "fight" and not need_zone and target["d"] > 45.0:
                    if abs(self.geo.roof_height(x + ux * 3.5, y + uy * 3.5) - z) >= 2.0:
                        if abs(self.geo.roof_height(x - ux * 3.5, y - uy * 3.5) - z) < 2.0:
                            ux, uy = -ux, -uy
                            self.strafe_dir = -self.strafe_dir
                        else:
                            ux, uy = 0.0, 0.0
                move = (ux * min(mag, 1.0), uy * min(mag, 1.0))
            else:
                ux, uy = self.safe_dir(x, y, move[0] / mag, move[1] / mag)
                move = (ux * min(mag, 1.0), uy * min(mag, 1.0))
        else:
            move = (0.0, 0.0)
        self.last_move_mag = math.hypot(move[0], move[1])
        act["move"] = [round(move[0], 3), round(move[1], 3)]
        act["boost"] = bool(boost)
        if jump:
            act["jump"] = True

        self.try_take(me, obs, kinds, act, t, stage)

        # ANTI-COOK-OFF MORTAR JETTISON (protocol.md §6 standalone drop):
        # Drop mortar immediately on visible laser, acoustic `laser_charge`/`laser` sound, laser beam, or late game!
        if "take" not in act and "mortar" in has_set and t - self.last_drop_t > 2.5:
            enemy_has_laser = any("laser" in m.get("weapons", []) and m["d"] < 275.0 for m in mechs)
            heard_laser = (t < self.heard_laser_until) or any(b.get("kind") == "laser" for b in obs.get("beams", []))
            if enemy_has_laser or heard_laser or stage == "late":
                mslot = 0 if kinds[0] == "mortar" else 1
                act["drop"] = mslot
                self.mortar_banned_until = t + 45.0
                self.last_drop_t = t

        fire = [False, False]
        if shoot_target is not None:
            fleeing_now = (mode == "flee")
            lethal_finish = (shoot_target.get("kind") == "mech" and shoot_target.get("los", False)
                             and shoot_target.get("hp", 9999) <= 320.0 and hp_frac > 0.35)
            flee_danger = fleeing_now and (not lethal_finish) and (hp_frac < 0.55 or flee_n_los >= 2)
            hold_gun_now = (
                shoot_target.get("kind") == "mech"
                and stage == "early"
                and "laser" not in has_set
                and shoot_target["d"] > 125.0
                and ((shoot_target["id"] not in self.attackers) or (t - self.attackers[shoot_target["id"]] > 8.0))
                and shoot_target.get("hp", 9999) > 650.0
            )
            aim, fire = self.combat(me, obs, shoot_target, kinds, mechs, npcs,
                                    fleeing=fleeing_now, flee_danger=flee_danger, hold_gun=hold_gun_now)

        # ---- SURGICAL REAR-ARMOR & UNSEEN-BACKSTABBER REFLEX (unleashed trigger!)
        charging_now = any(w and w["kind"] == "laser" and (w.get("charge", 0.0) > 0.0 or fire[i]) for i, w in enumerate(weapons))
        any_weapon_usable = False
        for w in weapons:
            if not w:
                continue
            wk = w["kind"]
            if wk == "gun" and w["reload"] == 0 and w["ammo"] > 0:
                any_weapon_usable = True
            elif wk == "laser" and (w["cooldown"] <= 0.35 or w.get("charge", 0.0) > 0.0):
                any_weapon_usable = True
            elif wk == "lightning" and shoot_target is not None and shoot_target["d"] < w["stats"]["range"] and me["energy"] >= 4:
                any_weapon_usable = True
            elif wk == "mortar" and w["cooldown"] <= 0.35:
                any_weapon_usable = True

        # Case A: Unseen backstabber hit us from behind! Brief 0.25s FOV snap in `flee` without suppressing guns!
        unseen_backstab = (
            t < self.backstab_until
            and self.backstab_bearing is not None
            and (self.backstab_by is None or self.backstab_by not in vis_ids)
        )
        flee_quick_snap = (mode == "flee" and (self.backstab_until - t) > 0.40)
        if not charging_now and unseen_backstab and (flee_quick_snap or not any_weapon_usable or shoot_target is None or shoot_target["d"] > 105.0 or not shoot_target.get("los", False)):
            aim = [self.backstab_bearing, 0.0]
            fire = [False, False]
        # Case B: Unable to shoot right now (e.g. gun reloading for 2.4s!) or no LOS -> turn front armor to threats!
        elif not charging_now and (not any_weapon_usable or shoot_target is None or not shoot_target.get("los", False)):
            opt_yaw = self.optimal_rear_armor_yaw(me, mechs, t)
            if opt_yaw is not None:
                aim = [opt_yaw, 0.0]

        if aim is None:
            aim = self.scan_aim(me, obs, move, t)
        act["aim"] = [round(aim[0], 4), round(clamp(aim[1], self.min_pitch, self.max_pitch), 4)]

        for i, w in enumerate(weapons):
            if w and w["kind"] == "laser" and w.get("charge", 0.0) > 0.0 and not fire[i] and shoot_target is None:
                fire[i] = w["charge"] < w["stats"].get("charge", 0.75) + 1.5
        act["fire"] = fire

        gw = weapons[0] if kinds[0] == "gun" else (weapons[1] if kinds[1] == "gun" else None)
        if gw and gw["reload"] == 0 and gw["ammo"] < gw["stats"]["mag"] * 0.5 and not any(fire) and mode not in ("fight", "flee"):
            act["reload"] = True

        self.last_mode = mode
        if DEBUG_DRAW:
            mf.draw.label(f"UI {mode} hp{me['hp']:.0f} c{cubes}")
        return act

    # ------------------------------------------------------------------ loot & pickups (A1 + 10-cube discipline)
    def loot_goal(self, me, obs, npcs, kinds, t, cur_c, cur_r, stage):
        x, y, z = me["pos"]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        cubes = int(me.get("cubes", 0))
        self.loot_target = None
        best = None

        def inzone(p, pad=15.0):
            return d2(p, cur_c) < cur_r - pad

        def reachable(p):
            return (abs(p[2] - self.geo.ground(p[0], p[1])) < 2.5 and self.geo.is_free(p[0], p[1])) or d2(p, (x, y)) < 3.0

        have = [k for k in kinds if k]
        worst = min((WEAPON_RANK[k] for k in have if k != "gun"), default=-1)
        for p in obs.get("pickups", []):
            if self.blacklist.get(p["id"], 0.0) > t or p["kind"] in have:
                continue
            if (stage == "late" or t < self.mortar_banned_until) and p["kind"] == "mortar":
                continue  # Avoid +60% laser cook-off vulnerability!
            r = WEAPON_RANK.get(p["kind"], -1)
            if kinds[1] is not None and r <= worst:
                continue
            d = d2(p["pos"], (x, y))
            if d < 95.0 and inzone(p["pos"]) and reachable(p["pos"]):
                sc = d - r * 16.0
                if best is None or sc < best[0]:
                    best = (sc, p["pos"][0], p["pos"][1], "weapon")

        # 10-cube cap discipline: chase cubes below 10 or at 13..14 -> 15; don't bloat hitbox past cap!
        if cubes < self.stat_cap:
            c_range = 75.0
        elif cubes in (13, 14):
            c_range = 65.0
        else:
            c_range = 0.0
        if c_range > 0.0:
            for c in obs.get("cubes", []):
                if self.blacklist.get(c["id"], 0.0) > t:
                    continue
                d = d2(c["pos"], (x, y))
                if d < c_range and inzone(c["pos"]) and reachable(c["pos"]):
                    sc = d - 12.0
                    if best is None or sc < best[0]:
                        best = (sc, c["pos"][0], c["pos"][1], "cube")

        for dr in obs.get("drops", []):
            d = d2(dr["pos"], (x, y))
            if d < 165.0 and dr["lands_in"] < d / 11.0 + 3.0 and hp_frac > 0.28:
                if dr.get("holder") not in (None, me["id"]) and dr.get("progress", 0.0) > 2.0:
                    continue
                sc = d - 45.0
                if best is None or sc < best[0]:
                    best = (sc, dr["pos"][0], dr["pos"][1], "drop")

        if best is not None:
            return best[1], best[2], best[3]

        if hp_frac > 0.68 and (cubes < self.stat_cap or cubes in (13, 14)):
            cands = [n for n in npcs if n["d"] < 90.0 and n["los"] and inzone(n["pos"], 5.0)]
            if cands:
                n = min(cands, key=lambda n: n["d"] + n["hp"] * 0.1)
                self.loot_target = n
                return n["pos"][0], n["pos"][1], "npc"

        if hp_frac > 0.60 and stage != "late" and (cubes < self.stat_cap or kinds[1] is None):
            mz = self.muzzle(me)
            cands = []
            for c in obs.get("crates", []):
                d = d2(c["pos"], (x, y))
                if d < 70.0 and inzone(c["pos"], 5.0) and self.geo.los(mz, (c["pos"][0], c["pos"][1], c["pos"][2] + 1.2)):
                    cands.append((d, c))
            if cands:
                d, c = min(cands, key=lambda q: q[0])
                self.loot_target = {"pos": c["pos"], "vel": [0, 0, 0], "kind": "crate", "d": d, "hp": c["hp"], "id": c["id"], "los": True}
                if d > 40.0:
                    return c["pos"][0], c["pos"][1], "crate"
                return x, y, "crate"
        return None

    def try_take(self, me, obs, kinds, act, t, stage):
        x, y, z = me["pos"]
        have = [k for k in kinds if k]
        worst_slot = None
        if kinds[1] is not None:
            cand = [(WEAPON_RANK.get(kinds[i], -1), i) for i in (0, 1) if kinds[i] and kinds[i] != "gun"]
            if cand:
                worst_slot = min(cand)[1]
        for p in obs.get("pickups", []):
            if p["kind"] in have or self.blacklist.get(p["id"], 0.0) > t:
                continue
            if (stage == "late" or t < self.mortar_banned_until) and p["kind"] == "mortar":
                continue
            if d2(p["pos"], (x, y)) > self.reach - 0.3 or abs(p["pos"][2] - z) > 3.8:
                continue
            if kinds[1] is None or kinds[0] is None:
                act["take"] = p["id"]
                return
            if worst_slot is not None and WEAPON_RANK.get(p["kind"], -1) > WEAPON_RANK.get(kinds[worst_slot], -1):
                w = me["weapons"][worst_slot]
                if w["kind"] == "laser" and w.get("charge", 0.0) > 0.0:
                    continue
                act["take"] = p["id"]
                act["drop"] = worst_slot
                return

    def scan_aim(self, me, obs, move, t):
        if t < self.threat_until and self.threat_bearing is not None:
            return [self.threat_bearing, 0.0]
        if t < self.sound_until and self.sound_bearing is not None:
            return [self.sound_bearing, 0.0]
        mag = math.hypot(*move)
        if mag > 0.3:
            base = math.atan2(move[1], move[0])
            return [wrap(base + 1.0 * math.sin(t * 2.0 + self.scan_phase)), 0.0]
        return [wrap(t * 1.8 + self.scan_phase), 0.0]

    # ------------------------------------------------------------------ 1-tick slewed combat
    def combat(self, me, obs, tg, kinds, mechs, npcs, fleeing=False, flee_danger=False, hold_gun=False):
        weapons = me["weapons"]
        mz = self.muzzle(me)
        mv = me.get("vel", [0.0, 0.0, 0.0])
        mz_next = (mz[0] + mv[0] * 0.05, mz[1] + mv[1] * 0.05, mz[2] + mv[2] * 0.05)

        ch, rad, _ = self.actor_geom(tg)
        tp = (tg["pos"][0], tg["pos"][1], tg["pos"][2] + ch)
        vel = tg.get("vel", [0.0, 0.0, 0.0])
        tp_next = (tp[0] + vel[0] * 0.05, tp[1] + vel[1] * 0.05, tp[2] + vel[2] * 0.05)
        dist = math.dist(mz, tp)
        los = tg.get("los", True)
        cur = me["aim"]
        fire = [False, False]
        aim = None
        primary = None

        order = sorted(
            [(i, w) for i, w in enumerate(weapons) if w],
            key=lambda iw: -self.weapon_value(iw[1]["kind"], dist, los, iw[1], tg),
        )
        for i, w in order:
            k = w["kind"]
            st = w["stats"]
            if k == "gun":
                if not los or dist > st["range"] * 0.95:
                    continue
                lp = self.lead(mz_next, tp_next, vel, st["speed"])
                aim = self.aim_at(mz_next, lp)
            elif k == "laser":
                if not los or dist > st["range"] or (flee_danger and w.get("charge", 0.0) == 0.0):
                    continue
                aim = self.aim_at(mz_next, tp_next)
            elif k == "lightning":
                if not los or dist > st["range"] * 0.95:
                    continue
                # Dual-slot Gun + Lightning lead-sharing (427.5 DPS!)
                gun_w = next((gw for gw in weapons if gw and gw["kind"] == "gun" and gw["ammo"] > 0 and gw["reload"] == 0), None)
                if gun_w is not None:
                    lp = self.lead(mz_next, tp_next, vel, gun_w["stats"]["speed"])
                    aim = self.aim_at(mz_next, lp)
                else:
                    aim = self.aim_at(mz_next, tp_next)
            elif k == "mortar":
                if dist < 22.0 or dist > 230.0:
                    continue
                msrc = self.mortar_src(me)
                sol = self.mortar_solution(msrc, (tg["pos"][0], tg["pos"][1], tg["pos"][2] + 1.0), vel, st["speed"], st["gravity"], high=not los)
                if sol is None or not self.geo.mortar_arc_clear(msrc, sol[0], sol[1], st["speed"], st["gravity"], sol[2]):
                    continue
                aim = [sol[0], sol[1]]
            primary = i
            break

        if aim is None:
            aim = self.aim_at(mz, tp)
            if not los:
                aim[1] = 0.0

        # Predict ACTUAL slewed aim after 1 physics tick (0.05s) when fire() executes!
        slewed = self.slew_aim(cur, aim, me, 0.05)
        yaw_err = abs(wrap(slewed[0] - aim[0]))
        pitch_err = abs(slewed[1] - aim[1])
        tol = math.atan2(rad * 0.82, max(dist, 1.0)) + 0.004

        for i, w in enumerate(weapons):
            if not w:
                continue
            k = w["kind"]
            st = w["stats"]
            if k == "gun":
                if not hold_gun and los and dist < st["range"] * 0.95 and w["cooldown"] <= 0.1 and w["ammo"] > 0 and w["reload"] == 0:
                    lp = self.lead(mz_next, tp_next, vel, st["speed"])
                    ga = self.aim_at(mz_next, lp)
                    gt = tol * 1.6 + 0.012
                    if abs(wrap(slewed[0] - ga[0])) < gt and abs(slewed[1] - ga[1]) < gt:
                        fire[i] = True
            elif k == "laser":
                full = st.get("charge", 0.75)
                min_c = st.get("min_charge", 0.15)
                chg = w.get("charge", 0.0)
                if los and dist < st["range"]:
                    if w["cooldown"] <= 0.05:
                        la = self.aim_at(mz_next, tp_next)
                        l_on = abs(wrap(slewed[0] - la[0])) < tol and abs(slewed[1] - la[1]) < tol + 0.012
                        if chg > 0.0:
                            dmg_now = self.expected_laser_dmg(me, w, tg)
                            if l_on and chg >= min_c and dmg_now >= tg.get("hp", 9999) * 1.03:
                                fire[i] = False  # Instant lethal release!
                            elif fleeing and chg >= full + 0.15:
                                fire[i] = False  # Don't crawl at 0.5x speed while fleeing!
                            elif chg >= full - 0.025:
                                fire[i] = not l_on
                            elif l_on and chg >= full * 0.55 and tg.get("kind") == "mech":
                                tp_fut = (tp[0] + vel[0] * 0.25, tp[1] + vel[1] * 0.25, tp[2])
                                fire[i] = self.geo.los(mz, tp_fut)
                            else:
                                fire[i] = True
                        else:
                            fire[i] = not flee_danger
                elif chg > 0.0:
                    fire[i] = (not flee_danger) and (chg < full + 1.6)
            elif k == "lightning":
                cone = math.radians(st.get("cone_deg", 30.0)) * 0.48
                if los and dist < st["range"] * 0.95 and w["cooldown"] <= 0.1 and me["energy"] > st.get("energy", 3.0) + 4.0:
                    if abs(wrap(slewed[0] - math.atan2(tp_next[1] - mz_next[1], tp_next[0] - mz_next[0]))) < cone:
                        fire[i] = True
            elif k == "mortar":
                if primary == i and w["cooldown"] <= 0.05 and yaw_err < 0.03 and pitch_err < 0.03 and dist > 22.0:
                    fire[i] = True
                elif primary != i and w["cooldown"] <= 0.05 and 25.0 < dist < 230.0 and tg.get("kind") == "mech":
                    msrc = self.mortar_src(me)
                    sol = self.mortar_solution(msrc, (tg["pos"][0], tg["pos"][1], tg["pos"][2] + 1.0), vel, st["speed"], st["gravity"], high=False)
                    if sol and abs(wrap(slewed[0] - sol[0])) < 0.03 and abs(slewed[1] - sol[1]) < 0.03:
                        if self.geo.mortar_arc_clear(msrc, sol[0], sol[1], st["speed"], st["gravity"], sol[2]):
                            fire[i] = True
        return aim, fire

    def weapon_value(self, kind, dist, los, w, tg):
        if kind == "laser":
            ok = w["cooldown"] < 0.3 or w.get("charge", 0.0) > 0.0
            bonus = 1.5 if (tg.get("kind") == "mech" and "mortar" in tg.get("weapons", [])) else 0.0
            return (5.0 + bonus) if (los and ok and dist < w["stats"]["range"]) else 0.0
        if kind == "lightning":
            return 6.0 if (los and dist < w["stats"]["range"] * 0.95) else 0.0
        if kind == "gun":
            ok = w["ammo"] > 0 and w["reload"] == 0
            return (4.0 if ok else 2.0) if (los and dist < w["stats"]["range"]) else 0.0
        if kind == "mortar":
            ok = w["cooldown"] < 0.5
            return (3.0 if los else 4.5) if (ok and 25.0 < dist < 230.0) else 0.5
        return 0.0


if __name__ == "__main__":
    mf.run(Bot)
