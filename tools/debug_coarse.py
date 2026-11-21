"""Inspect the coarse nav grid around a point: which coarse cells are 'free', their field values toward a goal,
and whether the fine grid can actually walk between neighbouring coarse cell centres.

usage: python tools/debug_coarse.py x y goal_x goal_y
"""
import math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from geo import Geo  # noqa: E402

x, y, gx, gy = map(float, sys.argv[1:5])
g = Geo(os.path.join(HERE, "arena"))
nav = g.coarse
nf = g.field(gx, gy, x, y)
f = nf[1]
j0, i0 = nav.cell(x, y)
print(f"coarse cell of ({x},{y}) = (j={j0}, i={i0}), centre {nav.cell_xy(j0, i0)}; using {'coarse' if nf[0] is nav else 'fine'} grid")
print("field values (row = north at top), '#' = blocked, '*' = our cell:")
for j in range(j0 + 3, j0 - 4, -1):
    row = []
    for i in range(i0 - 4, i0 + 5):
        v = f[j, i]
        s = "   #   " if not nav.free[j, i] else (f"{v:7.1f}" if np.isfinite(v) else "  inf  ")
        row.append(("*" if (j, i) == (j0, i0) else " ") + s)
    cy = nav.cell_xy(j, i0)[1]
    print(f"y={cy:7.1f} " + "".join(row))
print("x centres:  " + "".join(f"{nav.cell_xy(j0, i)[0]:8.1f}" for i in range(i0 - 4, i0 + 5)))
print("\ncoarse edges from our cell, checked with the fine grid (walk_line between cell centres):")
cx, cy = nav.cell_xy(j0, i0)
for dj in (-1, 0, 1):
    for di in (-1, 0, 1):
        if dj == di == 0:
            continue
        jj, ii = j0 + dj, i0 + di
        if not nav.free[jj, ii]:
            continue
        px, py = nav.cell_xy(jj, ii)
        print(f"  -> ({dj:+d},{di:+d}) centre ({px:6.1f},{py:6.1f}) field {f[jj, ii]:7.1f}  fine walkable: {g.walk_line(cx, cy, px, py)}")
print("\nfine-grid free fraction inside our coarse cell and its east neighbour:")
for di in (0, 1):
    xs = [nav.cell_xy(j0, i0 + di)[0] - 3 + k for k in range(6)]
    ys = [nav.cell_xy(j0, i0)[1] - 3 + k for k in range(6)]
    fr = np.mean([g.is_free(a, b) for a in xs for b in ys])
    print(f"  coarse ({j0},{i0 + di}): {fr:.2f}")
