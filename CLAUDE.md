# Mech Royale bot: context for Claude Code

## The competition
Builders "Mech Royale": write the AI for one mech in a 16-mech battle royale on the OSU campus map.
Rules are in the kit's `protocol.md` (mechfight/1 JSON lines on stdin/stdout). Kit: mechfight-kit.tar.gz.
- Submit a public GitHub repo with the Dockerfile at the top level (≤ 20 MB). Built on linux/amd64 with network; runs with no network,
  1 CPU, 512 MB, uid 65534, read-only disk (only /tmp writable), 50 ms per decision.
- Score = mean of (16 - place). Survival beats kills.
- Practice: 48 matches vs 15 house bots (one squad that hunts you). Baseline 1.15; the leader had 4.63 on Fri Nov 20.
- **Cutoff: Sun Nov 22, 8:00 AM Pacific.** Only 8 teams have entered, so there's no qualifying round and every team plays the 64-match
  final FFA, with expert house bots filling the empty slots. Practice scores don't count in the final.

## This repo
- `bot.py`: decisions (zone, hiding spot with cover, loot, combat, flee to break line of sight, upgrades).
- `geo.py`: 1 m height grids, line-of-sight raycasts, a 2 m nav grid and a 6 m coarse nav grid, scipy Dijkstra fields.
  One Dijkstra per tick at most (`geo.budget`), so decisions stay under ~20 ms.
- `mapprep.py`: builds `map.npz` from `arena/` at Docker build time.
- `tools/sim.py`: a rough local simulator with stand-in house bots. It is NOT the real server and is harsher than reality
  (the kit's example bot gets ~0.4 here vs 1.15 for real). Only use it for comparing bots.
  Usage: `MECH_STREAM=<kit>/check/stream.jsonl.gz python3 tools/sim.py practice 10 1` (or `ffa`).
  Set `SIM_BOT=example EXAMPLE_DIR=<dir with example_bot.py, with its mf.run line removed>` to run the example bot.

## Status
- v1 passes `check/check.py`. It was run as uid 65534 on 1 CPU with a memory cap (max 9.5 ms).
- The Docker image wasn't built in the previous environment, because Docker Hub was blocked there. Build and check it first:
  `docker build -t my-bot . && python3 <kit>/check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "<kit>/arena:/arena:ro" my-bot`
- Sim: ~3 mean points in practice mode vs ~0.4 for the example bot.

## Ideas not done yet
- Peek and corner fighting; roof camping (jet onto roofs, since ground mechs often can't see the middle of a roof).
- Dodge mortar shells using `obs.projectiles`; react when a visible enemy is `charging` a laser at us.
- A smarter weapon loadout (e.g. laser + lightning), and more care with supply drops.
- Tuning thresholds from real practice scores.
