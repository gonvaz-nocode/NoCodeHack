"""Measure what the proposed trajectory layer would cost: geo.los per call, a vectorised N-direction x K-step
exposure rollout against several threats, and the process's peak memory after building the map.

usage: python tools/bench_rollout.py
"""
import ctypes, math, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from geo import Geo  # noqa: E402


def peak_rss_mb():
    if os.name != "nt":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    c = PMC(); c.cb = ctypes.sizeof(PMC)
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
        return float("nan")
    return c.PeakWorkingSetSize / 2 ** 20


t0 = time.perf_counter()
g = Geo(os.path.join(HERE, "arena"))
print(f"Geo build {1000 * (time.perf_counter() - t0):.0f} ms, peak memory after build {peak_rss_mb():.0f} MB")
rng = np.random.default_rng(1)
free = np.argwhere(g.free)
pts = [g.cell_xy(*free[k]) for k in rng.integers(0, len(free), 400)]

# 1) single los calls, threat eye -> our centre of mass, 30-150 m apart
pairs = []
while len(pairs) < 2000:
    a, b = pts[rng.integers(len(pts))], pts[rng.integers(len(pts))]
    d = math.dist(a, b)
    if 30 < d < 150:
        pairs.append(((a[0], a[1], g.ground(*a) + 8), (b[0], b[1], g.ground(*b) + 4.5)))
t0 = time.perf_counter()
for a, b in pairs:
    g.los(a, b)
per = (time.perf_counter() - t0) / len(pairs) * 1e6
print(f"geo.los: {per:.0f} us per call (mean of {len(pairs)}, 30-150 m)")


# 2) vectorised rollout: N dirs x K steps x T threats, all rays sampled every 1.5 m in one numpy pass
def rollout(x, y, threats, N=8, K=10, step=1.17, ds=1.5):
    a = np.arange(N) * (2 * np.pi / N)
    k = np.arange(1, K + 1) * step
    px = x + np.cos(a)[:, None] * k[None, :]            # N x K
    py = y + np.sin(a)[:, None] * k[None, :]
    ii = np.clip(np.rint(px - g.x0).astype(np.int32), 0, g.nx - 1)
    jj = np.clip(np.rint(py - g.y0).astype(np.int32), 0, g.ny - 1)
    pz = g.terr[jj, ii] + 4.5
    exposed = np.zeros((N, K), bool)
    S = 100  # samples per ray (covers up to 150 m at 1.5 m)
    u = np.linspace(0, 1, S + 2)[1:-1]
    for tx, ty, tz in threats:                         # T is small (<= 15)
        rx = tx + (px[..., None] - tx) * u              # N x K x S
        ry = ty + (py[..., None] - ty) * u
        rz = tz + (pz[..., None] - tz) * u
        ri = np.clip(np.rint(rx - g.x0).astype(np.int32), 0, g.nx - 1)
        rj = np.clip(np.rint(ry - g.y0).astype(np.int32), 0, g.ny - 1)
        blocked = ((rz < g.terr[rj, ri] - 0.3) | ((rz <= g.top[rj, ri]) & (rz >= g.base[rj, ri]))).any(-1)
        exposed |= ~blocked
    return exposed.sum(1)


for T in (1, 3, 6):
    times = []
    for rep in range(60):
        x, y = pts[rep]
        thr = []
        for _ in range(T):
            q = pts[rng.integers(len(pts))]
            thr.append((q[0], q[1], g.ground(*q) + 8))
        t0 = time.perf_counter()
        rollout(x, y, thr)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    print(f"rollout 8 dirs x 10 steps vs {T} threat(s): median {times[30]:.2f} ms, worst {times[-1]:.2f} ms")
print(f"peak memory at end {peak_rss_mb():.0f} MB (limit 512 MB)")
