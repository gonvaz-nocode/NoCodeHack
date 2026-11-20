"""Rasterize world.json + terrain into grids used by the bot. Run at build time; falls back to runtime."""
import json, sys, os
import numpy as np

def load(map_dir):
    w = json.load(open(os.path.join(map_dir, "v2/world.json")))
    t = w["terrain"]
    nx, ny = t["nx"], t["ny"]
    terr = np.fromfile(os.path.join(map_dir, "v2/terrain.i16"), dtype="<i2").reshape(ny, nx).astype(np.float32) * 0.01
    return w, terr

def raster_poly(ring, holes, x0, y0, nx, ny, step=1.0):
    """Cell centers (x0+i*step, y0+j*step) inside polygon. Returns (j0,i0,mask)."""
    xs = np.array(ring[0::2]); ys = np.array(ring[1::2])
    i0 = max(0, int(np.floor((xs.min() - x0) / step))); i1 = min(nx - 1, int(np.ceil((xs.max() - x0) / step)))
    j0 = max(0, int(np.floor((ys.min() - y0) / step))); j1 = min(ny - 1, int(np.ceil((ys.max() - y0) / step)))
    if i1 < i0 or j1 < j0:
        return j0, i0, np.zeros((0, 0), bool)
    gx, gy = np.meshgrid(x0 + np.arange(i0, i1 + 1) * step, y0 + np.arange(j0, j1 + 1) * step)
    def inside(r):
        px = np.array(r[0::2]); py = np.array(r[1::2])
        res = np.zeros(gx.shape, bool)
        n = len(px)
        for k in range(n):
            ax, ay, bx, by = px[k], py[k], px[(k + 1) % n], py[(k + 1) % n]
            cond = (ay > gy) != (by > gy)
            with np.errstate(divide="ignore", invalid="ignore"):
                xint = ax + (gy - ay) * (bx - ax) / (by - ay)
            res ^= cond & (gx < xint)
        return res
    m = inside(ring)
    for h in holes or []:
        m &= ~inside(h)
    return j0, i0, m

def build(map_dir):
    w, terr = load(map_dir)
    t = w["terrain"]; x0, y0, nx, ny = t["minX"], t["minY"], t["nx"], t["ny"]
    top = np.full((ny, nx), -1e4, np.float32)    # highest solid top (non-bridge) per 1 m cell
    base = np.full((ny, nx), 1e4, np.float32)    # lowest solid base
    for p in w["prisms"]:
        if p["kind"] == "bridge":
            continue
        j0, i0, m = raster_poly(p["ring"], p["holes"], x0, y0, nx, ny)
        if m.size == 0:
            continue
        sl = (slice(j0, j0 + m.shape[0]), slice(i0, i0 + m.shape[1]))
        top[sl] = np.where(m, np.maximum(top[sl], p["top"]), top[sl])
        base[sl] = np.where(m, np.minimum(base[sl], p["base"]), base[sl])
    # walls for a walking mech: a solid that starts below head height and rises above a step
    wall = (top > terr + 1.2) & (base < terr + 9.0)
    # steep terrain
    gy, gx = np.gradient(terr)
    steep = np.hypot(gx, gy) > np.tan(np.radians(48))
    blocked = wall | steep
    return dict(terr=terr, top=top, base=base, blocked=blocked, x0=np.float32(x0), y0=np.float32(y0))

if __name__ == "__main__":
    md = sys.argv[1]; out = sys.argv[2]
    d = build(md)
    np.savez_compressed(out, **d)
    print("blocked frac", d["blocked"].mean(), file=sys.stderr)
