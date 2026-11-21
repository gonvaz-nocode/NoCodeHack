"""Ultra-fast map geometry v2: < 2ms coarse hide Dijkstra, batch LOS, peek-corners, canopy, 3D roofs."""
import math, os, sys
import numpy as np
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

S = 2  # fine nav cell size (m)
EXPOSURE_LAMBDA = 1.4


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Nav:
    """Walkable grid at cell size S with a sparse graph for fast bounded Dijkstra."""

    def __init__(self, infl, x0, y0, S):
        self.S = S
        self.x0 = x0
        self.y0 = y0
        ny, nx = infl.shape
        self.H, self.W = ny // S, nx // S
        free = ~infl[S // 2::S, S // 2::S][: self.H, : self.W]
        lab, n = ndimage.label(free, structure=np.ones((3, 3)))
        if n > 1:
            sizes = ndimage.sum(free, lab, range(1, n + 1))
            free = lab == (1 + int(np.argmax(sizes)))
        self.free = free
        H, W = self.H, self.W
        idx = np.arange(H * W).reshape(H, W)
        rows, cols, wts = [], [], []
        ys0, xs0 = np.nonzero(free)
        for dy, dx, c in [(0, 1, 1.0), (1, 0, 1.0), (1, 1, 1.4142), (1, -1, 1.4142)]:
            y2, x2 = ys0 + dy, xs0 + dx
            ok = (y2 >= 0) & (y2 < H) & (x2 >= 0) & (x2 < W)
            ys, xs, y2, x2 = ys0[ok], xs0[ok], y2[ok], x2[ok]
            ok = free[y2, x2]
            if dx and dy:
                ok &= free[ys, x2] & free[y2, xs]
            ys, xs, y2, x2 = ys[ok], xs[ok], y2[ok], x2[ok]
            rows.append(idx[ys, xs])
            cols.append(idx[y2, x2])
            wts.append(np.full(len(ys), c * S, dtype=np.float32))
        r = np.concatenate(rows)
        c = np.concatenate(cols)
        w = np.concatenate(wts)
        self.edges = (r, c, w)
        self.G = csr_matrix(
            (np.concatenate([w, w]), (np.concatenate([r, c]), np.concatenate([c, r]))),
            shape=(H * W, H * W),
        )
        self.Gw = None

    def set_exposure(self, expo, lam):
        r, c, w = self.edges
        e = expo.ravel()
        we = w * (1.0 + lam * 0.5 * (e[r] + e[c]))
        n = self.H * self.W
        self.Gw = csr_matrix(
            (np.concatenate([we, we]), (np.concatenate([r, c]), np.concatenate([c, r]))),
            shape=(n, n),
        )
        self.lam = lam

    def cell(self, x, y):
        S = self.S
        i = int((x - self.x0) / S)
        j = int((y - self.y0) / S)
        if i < 0:
            i = 0
        elif i >= self.W:
            i = self.W - 1
        if j < 0:
            j = 0
        elif j >= self.H:
            j = self.H - 1
        return j, i

    def cell_xy(self, j, i):
        S = self.S
        return self.x0 + i * S + S * 0.5, self.y0 + j * S + S * 0.5

    def nearest_free(self, x, y, rmax=16):
        j, i = self.cell(x, y)
        if self.free[j, i]:
            return j, i
        for r in range(1, rmax):
            j0, j1 = max(0, j - r), min(self.H - 1, j + r)
            i0, i1 = max(0, i - r), min(self.W - 1, i + r)
            sub = self.free[j0:j1 + 1, i0:i1 + 1]
            if sub.any():
                js, is_ = np.nonzero(sub)
                k = int(np.argmin((js + j0 - j) ** 2 + (is_ + i0 - i) ** 2))
                return int(js[k] + j0), int(is_[k] + i0)
        return j, i

    def dijkstra(self, j, i, limit=np.inf, weighted=False):
        G = self.Gw if weighted and self.Gw is not None else self.G
        return dijkstra(G, directed=True, indices=j * self.W + i, limit=limit).reshape(self.H, self.W).astype(np.float32)


class Geo:
    def __init__(self, map_dir, cache=None):
        d = None
        here = os.path.dirname(os.path.abspath(__file__))
        for p in [cache, os.path.join(here, "map.npz")]:
            if p and os.path.exists(p):
                try:
                    d = dict(np.load(p))
                    break
                except Exception:
                    d = None
        if d is None:
            sys.path.insert(0, here)
            import mapprep
            d = mapprep.build(map_dir)

        self.terr = np.ascontiguousarray(d["terr"], dtype=np.float32)
        self.top = np.ascontiguousarray(d["top"], dtype=np.float32)
        self.base = np.ascontiguousarray(d["base"], dtype=np.float32)
        self.x0 = float(d["x0"])
        self.y0 = float(d["y0"])
        self.ny, self.nx = self.terr.shape

        blocked = d["blocked"]
        yy, xx = np.mgrid[-3:4, -3:4]
        disk = (xx * xx + yy * yy) <= 2.85 * 2.85
        infl = ndimage.binary_dilation(blocked, structure=disk)
        self.fine = Nav(infl, self.x0, self.y0, 2)
        self.coarse = Nav(infl, self.x0, self.y0, 6)
        self.H, self.W, self.free = self.fine.H, self.fine.W, self.fine.free

        # Tall buildings (hard cover)
        tall = (self.top - self.terr > 5.5) & (self.base < self.terr + 9.0)
        tall_c = tall[S // 2::S, S // 2::S][: self.H, : self.W].astype(np.float32)
        self.cover = ndimage.uniform_filter(tall_c, size=9)

        # Crisp peek-cover adjacency (within 2..8m of a tall building wall)
        wall_near = ndimage.binary_dilation(tall_c > 0.5, iterations=4)
        wall_touch = ndimage.binary_dilation(tall_c > 0.5, iterations=1)
        self.peek_adj = (wall_near & self.free).astype(np.float32) + 0.5 * (wall_touch & self.free).astype(np.float32)

        # Coarse versions for <1.5 ms hide/patrol queries (9x faster than fine!)
        k = self.coarse.S // S
        self.c_cover = ndimage.uniform_filter(self.cover, size=k)[k // 2::k, k // 2::k][: self.coarse.H, : self.coarse.W]
        self.c_peek = ndimage.uniform_filter(self.peek_adj, size=k)[k // 2::k, k // 2::k][: self.coarse.H, : self.coarse.W]
        self.c_ii = np.arange(self.coarse.W, dtype=np.float32) * self.coarse.S + self.x0 + self.coarse.S * 0.5
        self.c_jj = np.arange(self.coarse.H, dtype=np.float32) * self.coarse.S + self.y0 + self.coarse.S * 0.5

        # Climbable rooftops (3D vertical awareness for real server)
        roof_raw = (self.top > self.terr + 2.5) & (self.top - self.terr <= 52.0) & (self.top > -1e3)
        self.roof_1m = roof_raw
        self.roof_inner = ndimage.binary_erosion(roof_raw, structure=disk)

        # Canopy map (blocks enemy vision >45m, does NOT block shots)
        self.canopy = self._canopy(map_dir)
        self.c_canopy = ndimage.uniform_filter(self.canopy, size=k)[k // 2::k, k // 2::k][: self.coarse.H, : self.coarse.W]
        shelter = np.maximum(np.clip(self.cover / 0.25, 0, 1), ndimage.uniform_filter(self.canopy, size=3))
        self.exposure = (1.0 - shelter).astype(np.float32)
        self.fine.set_exposure(self.exposure, EXPOSURE_LAMBDA)
        self.coarse.set_exposure(
            ndimage.uniform_filter(self.exposure, size=k)[k // 2::k, k // 2::k][: self.coarse.H, : self.coarse.W],
            EXPOSURE_LAMBDA,
        )

        self.fields = {}
        self.cache_n = 18
        self.budget = 1

    def _canopy(self, map_dir):
        H, W = self.H, self.W
        out = np.zeros((H, W), np.float32)
        try:
            import json
            tr = json.load(open(os.path.join(map_dir, "v2", "world.json")))["trees"]
            f, d = tr["fields"], tr["data"]
            n = len(f)
            a = np.array(d, np.float32).reshape(-1, n)
            cx = a[:, f.index("crownX")] if "crownX" in f else a[:, f.index("x")]
            cy = a[:, f.index("crownY")] if "crownY" in f else a[:, f.index("y")]
            cr = a[:, f.index("crownR")]
        except Exception:
            return out
        jj, ii = np.mgrid[0:H, 0:W]
        for x, y, r in zip(cx, cy, cr):
            r = max(float(r), 1.0)
            i0 = max(0, int((x - r - self.x0) / S))
            i1 = min(W - 1, int((x + r - self.x0) / S))
            j0 = max(0, int((y - r - self.y0) / S))
            j1 = min(H - 1, int((y + r - self.y0) / S))
            if i1 < i0 or j1 < j0:
                continue
            sx = self.x0 + ii[j0:j1 + 1, i0:i1 + 1] * S + S * 0.5 - x
            sy = self.y0 + jj[j0:j1 + 1, i0:i1 + 1] * S + S * 0.5 - y
            out[j0:j1 + 1, i0:i1 + 1] = np.maximum(
                out[j0:j1 + 1, i0:i1 + 1], (sx * sx + sy * sy <= r * r).astype(np.float32)
            )
        return out

    # ---------- grids
    def cell(self, x, y):
        return self.fine.cell(x, y)

    def cell_xy(self, j, i):
        return self.fine.cell_xy(j, i)

    def nearest_free(self, x, y, rmax=16):
        return self.fine.nearest_free(x, y, rmax)

    def ground(self, x, y):
        i = int(round(x - self.x0))
        j = int(round(y - self.y0))
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return float(self.terr[j, i])
        return 0.0

    def roof_height(self, x, y):
        i = int(round(x - self.x0))
        j = int(round(y - self.y0))
        if 0 <= i < self.nx and 0 <= j < self.ny and self.roof_1m[j, i]:
            return float(self.top[j, i])
        return -1e4

    def is_free(self, x, y):
        j, i = self.cell(x, y)
        return bool(self.free[j, i])

    def peek_score(self, x, y):
        j, i = self.cell(x, y)
        return float(self.peek_adj[j, i])

    def cover_score(self, x, y):
        j, i = self.cell(x, y)
        return float(self.cover[j, i])

    def canopy_at(self, x, y):
        j, i = self.cell(x, y)
        return float(self.canopy[j, i])

    def nearest_ground_edge(self, rx, ry, rmax=22):
        """Find nearest walkable ground cell adjacent to a rooftop point (for real-server jet launch)."""
        j, i = self.fine.nearest_free(rx, ry, rmax=rmax)
        return self.fine.cell_xy(j, i)

    # ---------- fast line of sight
    def los(self, a, b, step=1.5):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        dz = b[2] - a[2]
        dist = math.hypot(dx, dy)
        n = int(dist / step)
        if n < 2:
            return True
        nx, ny = self.nx, self.ny
        x0, y0 = self.x0, self.y0
        if n <= 36:
            inv = 1.0 / n
            terr = self.terr
            top = self.top
            base = self.base
            ax, ay, az = a[0] - x0, a[1] - y0, a[2]
            for k in range(1, n):
                t = k * inv
                xi = int(ax + dx * t + 0.5)
                yi = int(ay + dy * t + 0.5)
                if 0 <= xi < nx and 0 <= yi < ny:
                    z = az + dz * t
                    if z < terr[yi, xi] - 0.3:
                        return False
                    if base[yi, xi] <= z <= top[yi, xi]:
                        return False
            return True
        t = np.linspace(0.0, 1.0, n + 1, dtype=np.float32)[1:-1]
        xs = np.rint(a[0] + dx * t - x0).astype(np.int32)
        ys = np.rint(a[1] + dy * t - y0).astype(np.int32)
        zs = a[2] + dz * t
        ok = (xs >= 0) & (xs < nx) & (ys >= 0) & (ys < ny)
        xs = xs[ok]
        ys = ys[ok]
        zs = zs[ok]
        if (zs < self.terr[ys, xs] - 0.3).any():
            return False
        return not ((zs <= self.top[ys, xs]) & (zs >= self.base[ys, xs])).any()

    def los_batch_from_origins(self, origins_xyz, targets_xyz, step=2.2):
        """Vectorized LOS for all pairs (E origins x C targets). Returns bool (E, C)."""
        E = len(origins_xyz)
        C = len(targets_xyz)
        if E == 0 or C == 0:
            return np.zeros((E, C), dtype=bool)
        orig = np.asarray(origins_xyz, dtype=np.float32)[:, None, :]
        tgts = np.asarray(targets_xyz, dtype=np.float32)[None, :, :]
        d = tgts - orig
        max_d = float(np.hypot(d[:, :, 0], d[:, :, 1]).max())
        max_n = int(max_d / step)
        if max_n < 2:
            return np.ones((E, C), dtype=bool)
        max_n = min(max_n, 72)
        ts = np.linspace(0.0, 1.0, max_n + 1, dtype=np.float32)[1:-1]
        xs = np.rint(orig[:, :, 0:1] + d[:, :, 0:1] * ts[None, None, :] - self.x0).astype(np.int32)
        ys = np.rint(orig[:, :, 1:2] + d[:, :, 1:2] * ts[None, None, :] - self.y0).astype(np.int32)
        zs = orig[:, :, 2:3] + d[:, :, 2:3] * ts[None, None, :]
        ok = (xs >= 0) & (xs < self.nx) & (ys >= 0) & (ys < self.ny)
        xc = np.clip(xs, 0, self.nx - 1)
        yc = np.clip(ys, 0, self.ny - 1)
        hit = ok & ((zs < self.terr[yc, xc] - 0.3) | ((zs <= self.top[yc, xc]) & (zs >= self.base[yc, xc])))
        return ~hit.any(axis=2)

    def mortar_arc_clear(self, src, yaw, pitch, speed, g, T, samples=12):
        vx = speed * math.cos(pitch) * math.cos(yaw)
        vy = speed * math.cos(pitch) * math.sin(yaw)
        vz = speed * math.sin(pitch)
        nx, ny = self.nx, self.ny
        x0, y0 = self.x0, self.y0
        terr = self.terr
        top = self.top
        base = self.base
        for k in range(1, samples):
            t = T * 0.90 * (k / samples)
            px = src[0] + vx * t
            py = src[1] + vy * t
            pz = src[2] + vz * t - 0.5 * g * t * t
            xi = int(px - x0 + 0.5)
            yi = int(py - y0 + 0.5)
            if 0 <= xi < nx and 0 <= yi < ny:
                if pz < terr[yi, xi] + 0.2:
                    return False
                if base[yi, xi] <= pz <= top[yi, xi] + 0.3:
                    return False
        return True

    def walk_line(self, ax, ay, bx, by):
        d = math.hypot(bx - ax, by - ay)
        n = max(2, int(d / 1.5))
        free = self.free
        cell = self.fine.cell
        dx = bx - ax
        dy = by - ay
        inv = 1.0 / n
        for k in range(1, n + 1):
            t = k * inv
            j, i = cell(ax + dx * t, ay + dy * t)
            if not free[j, i]:
                return False
        return True

    # ---------- navigation
    def field(self, gx, gy, fx=None, fy=None, weighted=False):
        far = fx is not None and math.hypot(gx - fx, gy - fy) > 150
        nav = self.coarse if far else self.fine
        j, i = nav.nearest_free(gx, gy, 40)
        key = (nav.S, j, i, bool(weighted))
        f = self.fields.get(key)
        if f is not None and fx is not None:
            jj, ii = nav.nearest_free(fx, fy)
            if not np.isfinite(f[jj, ii]):
                f = None
        if f is None:
            if self.budget <= 0:
                return None
            self.budget -= 1
            lim = np.inf
            if fx is not None and not far:
                lim = (math.hypot(gx - fx, gy - fy) * 1.8 + 90) * (1.0 + EXPOSURE_LAMBDA if weighted else 1.0)
            f = nav.dijkstra(j, i, lim, weighted)
            if len(self.fields) > self.cache_n:
                self.fields.pop(next(iter(self.fields)))
            self.fields[key] = f
        return nav, f

    def coarse_dist_field_from(self, x, y, limit=np.inf):
        """9x faster distance field on 6m coarse grid (< 1.5 ms!)."""
        self.budget -= 1
        j, i = self.coarse.nearest_free(x, y, 25)
        return self.coarse.dijkstra(j, i, limit)

    def dist_field_from(self, x, y, limit=np.inf):
        self.budget -= 1
        j, i = self.nearest_free(x, y)
        return self.fine.dijkstra(j, i, limit)

    def steer(self, nf, x, y, look=14):
        nav, f = nf
        H, W = nav.H, nav.W
        j, i = nav.cell(x, y)
        if not nav.free[j, i] or not np.isfinite(f[j, i]):
            best = None
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    jj, ii = j + dj, i + di
                    if (dj or di) and 0 <= jj < H and 0 <= ii < W and nav.free[jj, ii] and np.isfinite(f[jj, ii]):
                        cx, cy = nav.cell_xy(jj, ii)
                        d = math.hypot(cx - x, cy - y)
                        score = float(f[jj, ii]) + d + (0.0 if self.walk_line(x, y, cx, cy) else 1e4)
                        if best is None or score < best[0]:
                            best = (score, cx, cy, float(f[jj, ii]))
            if best:
                dx, dy = best[1] - x, best[2] - y
                d = max(math.hypot(dx, dy), 1e-3)
                return (dx / d, dy / d), best[3]
            return None, 1e9
        rem = float(f[j, i])
        cj, ci = j, i
        tx, ty = x, y
        steps = max(4, look // nav.S)
        for _ in range(steps):
            best = (float(f[cj, ci]), cj, ci)
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    jj, ii = cj + dj, ci + di
                    if 0 <= jj < H and 0 <= ii < W and f[jj, ii] < best[0]:
                        best = (float(f[jj, ii]), jj, ii)
            if (best[1], best[2]) == (cj, ci):
                break
            cj, ci = best[1], best[2]
            cx, cy = nav.cell_xy(cj, ci)
            if self.walk_line(x, y, cx, cy):
                tx, ty = cx, cy
            else:
                break
        dx, dy = tx - x, ty - y
        d = math.hypot(dx, dy)
        if d < 0.2:
            return None, rem
        return (dx / d, dy / d), rem
