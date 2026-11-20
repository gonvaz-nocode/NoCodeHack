"""Map knowledge: terrain, line of sight, navigation distance fields, cover."""
import math, os, sys, time
import numpy as np
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

S = 2  # nav cell size (m)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Nav:
    """Walkable grid at cell size S with a graph for Dijkstra."""

    def __init__(self, infl, x0, y0, S):
        self.S = S; self.x0 = x0; self.y0 = y0
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
            rows.append(idx[ys, xs]); cols.append(idx[y2, x2]); wts.append(np.full(len(ys), c * S))
        r = np.concatenate(rows); c = np.concatenate(cols); w = np.concatenate(wts)
        self.G = csr_matrix((np.concatenate([w, w]), (np.concatenate([r, c]), np.concatenate([c, r]))), shape=(H * W, H * W))

    def cell(self, x, y):
        S = self.S
        i = int((x - self.x0) / S); j = int((y - self.y0) / S)
        return min(max(j, 0), self.H - 1), min(max(i, 0), self.W - 1)

    def cell_xy(self, j, i):
        S = self.S
        return self.x0 + i * S + S / 2, self.y0 + j * S + S / 2

    def nearest_free(self, x, y, rmax=12):
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

    def dijkstra(self, j, i, limit=np.inf):
        return dijkstra(self.G, directed=True, indices=j * self.W + i, limit=limit).reshape(self.H, self.W).astype(np.float32)


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
        self.terr = d["terr"]; self.top = d["top"]; self.base = d["base"]
        self.x0 = float(d["x0"]); self.y0 = float(d["y0"])
        self.ny, self.nx = self.terr.shape
        blocked = d["blocked"]
        yy, xx = np.mgrid[-3:4, -3:4]
        disk = (xx * xx + yy * yy) <= 2.9 * 2.9
        infl = ndimage.binary_dilation(blocked, structure=disk)
        self.fine = Nav(infl, self.x0, self.y0, 2)
        self.coarse = Nav(infl, self.x0, self.y0, 6)
        self.H, self.W, self.free = self.fine.H, self.fine.W, self.fine.free
        tall = (self.top - self.terr > 6.0) & (self.base < self.terr + 9)
        tall_c = tall[S // 2::S, S // 2::S][: self.H, : self.W].astype(np.float32)
        self.cover = ndimage.uniform_filter(tall_c, size=9)
        self.fields = {}
        self.cache_n = 10
        self.budget = 1

    # ---------- grids
    def cell(self, x, y):
        return self.fine.cell(x, y)

    def cell_xy(self, j, i):
        return self.fine.cell_xy(j, i)

    def nearest_free(self, x, y, rmax=12):
        return self.fine.nearest_free(x, y, rmax)

    def ground(self, x, y):
        i = int(round(x - self.x0)); j = int(round(y - self.y0))
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return float(self.terr[j, i])
        return 0.0

    def is_free(self, x, y):
        j, i = self.cell(x, y)
        return bool(self.free[j, i])

    # ---------- line of sight
    def los(self, a, b, step=1.5):
        """True if the segment a->b (3D) is not blocked by buildings or terrain."""
        dx = b[0] - a[0]; dy = b[1] - a[1]; dz = b[2] - a[2]
        dist = math.hypot(dx, dy)
        n = int(dist / step)
        if n < 2:
            return True
        t = np.linspace(0.0, 1.0, n + 1)[1:-1]
        xs = np.rint(a[0] + dx * t - self.x0).astype(np.int32)
        ys = np.rint(a[1] + dy * t - self.y0).astype(np.int32)
        zs = a[2] + dz * t
        ok = (xs >= 0) & (xs < self.nx) & (ys >= 0) & (ys < self.ny)
        xs = xs[ok]; ys = ys[ok]; zs = zs[ok]
        if (zs < self.terr[ys, xs] - 0.3).any():
            return False
        return not ((zs <= self.top[ys, xs]) & (zs >= self.base[ys, xs])).any()

    def walk_line(self, ax, ay, bx, by):
        """Straight walkable line on the fine nav grid."""
        d = math.hypot(bx - ax, by - ay)
        n = max(2, int(d / 1.5))
        free = self.free; cell = self.fine.cell
        for k in range(1, n + 1):
            t = k / n
            j, i = cell(ax + (bx - ax) * t, ay + (by - ay) * t)
            if not free[j, i]:
                return False
        return True

    # ---------- navigation
    def field(self, gx, gy, fx=None, fy=None):
        """(nav, distance-to-goal field), cached by goal cell. Far goals use the coarse grid.
        Returns None if it would cost a second Dijkstra this tick."""
        far = fx is not None and math.hypot(gx - fx, gy - fy) > 160
        nav = self.coarse if far else self.fine
        j, i = nav.nearest_free(gx, gy, 40)
        key = (nav.S, j, i)
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
                lim = math.hypot(gx - fx, gy - fy) * 2.0 + 100
            f = nav.dijkstra(j, i, lim)
            if len(self.fields) > self.cache_n:
                self.fields.pop(next(iter(self.fields)))
            self.fields[key] = f
        return nav, f

    def dist_field_from(self, x, y, limit=np.inf):
        self.budget -= 1
        j, i = self.nearest_free(x, y)
        return self.fine.dijkstra(j, i, limit)

    def steer(self, nf, x, y, look=14):
        """Direction (unit xy) to follow field nf=(nav, f) from (x,y). Returns (dir, remaining)."""
        nav, f = nf
        j, i = nav.cell(x, y)
        if not nav.free[j, i] or not np.isfinite(f[j, i]):
            j, i = nav.nearest_free(x, y)
            cx, cy = nav.cell_xy(j, i)
            d = math.hypot(cx - x, cy - y)
            if d < 0.3:
                return None, float("inf")
            return ((cx - x) / d, (cy - y) / d), float(f[j, i]) + d
        rem = float(f[j, i])
        path = [(j, i)]
        H, W = nav.H, nav.W
        for _ in range(look if nav.S == 2 else 6):
            best = None; bv = f[j, i]
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    if dj == 0 and di == 0:
                        continue
                    jj, ii = j + dj, i + di
                    if 0 <= jj < H and 0 <= ii < W:
                        v = f[jj, ii]
                        if v < bv - 1e-4:
                            bv = v; best = (jj, ii)
            if best is None:
                break
            j, i = best
            path.append(best)
        tgt = None
        for p in reversed(path[1:]):
            px, py = nav.cell_xy(*p)
            if self.walk_line(x, y, px, py):
                tgt = p
                break
        if tgt is None:
            tgt = path[1] if len(path) > 1 else path[0]
        px, py = nav.cell_xy(*tgt)
        d = math.hypot(px - x, py - y)
        if d < 0.2:
            return None, rem
        return ((px - x) / d, (py - y) / d), rem
