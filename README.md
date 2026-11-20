# survivor: a Mech Royale bot

Python bot for the Builders Mech Royale track (mechfight/1).

- `bot.py`: decision logic (zone, cover, looting, combat, upgrades)
- `geo.py`: map grids, line of sight, Dijkstra navigation fields
- `mapprep.py`: rasterises `arena/v2` into `map.npz` at build time
- `mechfight.py`: the kit's protocol wrapper, unchanged

Dependencies are pinned in the Dockerfile (numpy 2.5.3, scipy 1.18.1).
