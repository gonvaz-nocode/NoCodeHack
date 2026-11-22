"""Survivor bot for Mech Royale (mechfight/1).

Priorities: stay inside the zone, stay near cover, loot early, fight facing the enemy while strafing,
break line of sight when losing, and bank HP (cubes, plating) for the end-game zone.
"""
import math, os, random, sys, time
import mechfight as mf
from geo import Geo

TAU = 2 * math.pi
WEAPON_RANK = {"laser": 3, "lightning": 2, "mortar": 1, "gun": 0}

# Tunable numbers (tools/tune.py searches these in the sim). Defaults are the hand-picked v1 values; a JSON object in
# SURVIVOR_PARAMS overrides any of them (only the tuner sets it; the match server does not).
PARAMS = {
    # preferred fighting range by best weapon held
    "rng_laser_lo": 60.0, "rng_laser_hi": 160.0, "rng_light_lo": 12.0, "rng_light_hi": 32.0,
    "rng_mortar_lo": 50.0, "rng_mortar_hi": 130.0, "rng_gun_lo": 25.0, "rng_gun_hi": 70.0,
    "radial": 0.8,                                   # how hard to close / open the range
    # when to flee
    "flee_hp1": 0.35, "flee_ratio1": 1.1, "flee_hp2": 0.5, "flee_ratio2": 1.3,
    "flee_natt": 2.0, "flee_nlos": 3.0, "flee_hold": 2.0, "flee_boost_e": 15.0,
    # fight movement
    "strafe_min": 0.3, "strafe_max": 0.9, "fight_boost_e": 45.0, "fight_boost_d": 150.0,
    # target choice: score = d + hp*ts_hp - attacker*ts_att + no_los*ts_nolos - sticky*ts_sticky (lowest wins)
    "ts_hp": 0.04, "ts_att": 60.0, "ts_nolos": 80.0, "ts_sticky": 45.0,
    # zone
    "zone_lead": 25.0, "zone_edge": 8.0, "zone_margin": 30.0, "zone_margin_frac": 0.35,
    "hide_cover": 60.0, "hide_dist": 0.08, "hide_center": 12.0,
    # loot
    "loot_w_range": 90.0, "loot_w_rank": 15.0, "loot_c_range": 70.0, "loot_c_bonus": 10.0,
    "crate_hp": 0.6, "crate_range": 70.0,
    "rank_laser": 3.0, "rank_light": 2.0, "rank_mortar": 1.0,
    # gun trigger tolerance multiplier
    "gun_tol": 1.6,
    # A9 "gunwise": gun policy by game stage (stage = early while the zone is wider than stage_mid_r, late below
    # stage_late_r): longest range we open fire at unattacked, the least hit chance worth a shot, reload threshold
    "stage_mid_r": 300.0, "stage_late_r": 100.0,
    "gun_maxd_early": 110.0, "gun_maxd_mid": 170.0, "gun_maxd_late": 230.0,
    "gun_phit_early": 0.45, "gun_phit_mid": 0.30, "gun_phit_late": 0.15,
    "gun_reload_early": 0.75, "gun_reload_mid": 0.5, "gun_reload_late": 0.34,
    # B2/B3 "phased": hide + loot ("build") then hunt. Practice: hunt from ph_prac_t s, or once we hold a second real
    # weapon and ph_prac_cubes cubes. Free-for-all: hunt once ph_ffa_alive or fewer mechs remain, or in the late stage.
    "ph_prac_t": 45.0, "ph_prac_cubes": 2.0, "ph_ffa_alive": 6.0, "ph_loot_w": 1.3,
    # C5 "heal": start healing under heal_lo, stop at heal_hi; don't start fights under heal_start
    "heal_lo": 0.5, "heal_hi": 0.9, "heal_start": 0.7,
    # F1 "berserk": retreat (out of sight, behind cover) only under bz_flee HP; go back in at bz_back
    "bz_flee": 0.25, "bz_back": 0.5,
    # G4 hold-spot range factor (hold for targets within want_hi * hold_range); H1 roof escape threshold
    "hold_range": 1.1, "roofesc_hp": 0.35,
    # third party (AB): bonus for enemies busy fighting someone else, and the range band we hold on them
    "tp_bonus": 60.0, "tp_lo": 50.0, "tp_hi": 110.0,
    # laser corner peek (AF): nearest target range for it
    "lp_lo": 45.0,
    # hold ground (AK): don't trek to a hide spot further than this when idle
    "sp_d": 60.0,
    # AL3 strafe rhythm (seconds per direction)
    "strafe_T": 2.2,
    # target bonus for a mech under 30% HP (0 = off)
    "ts_low": 0.0,
    # UA edge patrol: how far inside the next circle's edge to walk
    "ep_in": 25.0,
    # AC1 finish (commit to a won fight) and AC2 travel boost thresholds
    "fin_t": 0.3, "fin_me": 0.5, "tb_e": 40.0,
    # I-series repair: heal when under rep_lo, or under rep_hi inside the quiet window after a kill (rep_kill s)
    "rep_lo": 0.55, "rep_hi": 0.9, "rep_kill": 8.0,
    # X10 profile: easy kills (hp under easy_hp, within easy_d, back turned, or shooting us); harvest until a second
    # weapon and harvest_cubes cubes; heal threshold rises to heal_lo_late once the zone is small
    "easy_hp": 0.4, "easy_d": 45.0, "harvest_cubes": 2.0, "heal_lo_late": 0.5,
}

# Named versions -> behaviour switches. A1 is the original v1 (git commit f2c923f), not reproducible by switches.
# DEFAULT_VERSION is what plays in a match; SURVIVOR_VERSION (or a raw SURVIVOR_FLAGS list) overrides it for testing.
VERSIONS = {
    "A2": "",                     # A1 + zone-trap fix (commit cbc06b1)
    "A3": "pick",                 # fight only mechs that threaten us
    "A4": "cover",                # cover-to-cover routing
    "A5": "creep",                # walk silently near enemies
    "A6": "pick,cover,creep",
    "A7": "flank",                # hit backs hard, shy when faced
    "A8": "ambush,creep",         # hide more; attack only lone mechs facing away
    "A9": "gunwise",              # stage-aware gun use
    "B1": "center",               # walk to a half-sheltered spot near the map centre, then to the zone through cover
    "B2": "phased,gunwise",       # A9 guns; hide + loot to grow while others thin out, then turn aggressive (when depends
                                  # on whether this is practice or a free-for-all, detected from who is killing whom)
    "B3": "phased,switch,gunwise",  # A9 guns; aggressive in practice, A8-style hiding for the rest of a free-for-all
    # experiments on top of B3 (their settings are in VERSION_PARAMS)
    "B4": "phased,switch,gunwise",           # more careful gun than A9
    "B5": "phased,switch,gunwise",           # less careful gun than A9 (between A9 and the original)
    "B6": "phased,switch,gunwise",           # Z100 tuner's best settings (sim-tuned, 3 generations)
    "B7": "phased,switch,gunwise,endgame",   # end-game survival push: drops, cubes, keep HP for the last circles
    "B8": "phased,switch,gunwise",           # B3 corrected: practice play identical to A9; free-for-all needs 2 deaths
    "B9": "phased,switch,gunwise,timed",     # B8 with timing-aware detection: 2 deaths by 30 s, 3 by 60 s, or 6 any time
    # the B4-B7 experiments rebuilt on B9 (B3's detection false-alarmed in practice)
    "C1": "phased,switch,gunwise,timed",           # B9 + more careful gun (B4's settings)
    "C2": "phased,switch,gunwise,timed",           # B9 + less careful gun (B5's settings)
    "C3": "phased,switch,gunwise,timed",           # B9 + Z100 tuner's settings (B6's)
    "C4": "phased,switch,gunwise,timed,endgame",   # B9 + end-game survival push (B7's)
    # practice-score experiments on A9 (the best real base): one change each
    "C5": "gunwise,heal",       # heal between fights: hide until 90% HP after dropping under 50%; no new fights under 70%
    "C6": "gunwise",            # one fight at a time: break line of sight once two enemies can see us
    "C7": "gunwise",            # laser kiting: hold 120-150 m with a laser
    "C8": "gunwise,dodge",      # sidestep charging lasers aimed at us and incoming mortar shells
    "C9": "gunwise,heal",       # C5 + C6: heal between fights, and one fight at a time
    "E1": "gunwise,dodge,heal",          # C8 (best real 3.88) + heal between fights
    "E2": "gunwise,dodge",               # C8 + Z100 tuner's settings
    "E3": "gunwise,dodge,dodgeb",        # C8 + dodge bullets too
    "F1": "gunwise,dodge,berserk",       # C8, extremely aggressive: chase hard, retreat only under 25% HP (back at 50%)
    "F2": "gunwise,dodge,berserk",       # F1, retreat under 35% (back at 60%)
    "G1": "gunwise,dodge,laserfirst",             # C8 + laser first
    "G2": "gunwise,dodge,fightcover",             # C8 + fight from a covered firing spot + head early for the next circle
    "G3": "gunwise,dodge,fightcover,laserfirst",  # G2 + laser first
    "G4": "gunwise,dodge,holdspot",           # C8 + fight from the pre-chosen covered spot when the target is in range
    "G5": "gunwise,dodge,adapt",              # C8 + learn press vs space during the match (damage out - in)
    "G6": "gunwise,dodge,holdspot,adapt",     # G4 + G5
    "UL1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,twofire",  # UE2 + flee when 2+ enemies are shooting us under 65% HP
    "UK1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,scouthop",  # UE2 + scouting hop over rooftops when idle
    "UK2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,scouthop,stalk",  # UK1 + stage an approach to a spotted enemy
    "UJ1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,cornerfight",  # UE2 + fight from corners with an escape step
    "UI1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,treefire,lateonly",  # UE2 + blind fire into trees in the last circles
    "UI2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,treefire,memfire,lateonly",  # UI1 + keep firing where a lost target should be, last circles
    "UH1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,chainaim",  # UE2 + UG2 (both 5.58)
    "UH2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,chainaim,triangle",  # UH1 + weapon triangle
    "UG1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,jumpjuke",  # UE2 + jump to slip laser beams aimed at us
    "UG2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,chainaim",  # UE2 + lightning aimed at grouped enemies
    "UG3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,upg4",  # UE2 + sensors-first upgrade order
    "UG4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst,mortarlight",  # UE2 + mortar targets lightning carriers
    "UF1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,cookoff,shortfirst",  # UC2 + cook-off targeting + lightning short first
    "UF2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,cookoff,shortfirst,triangle",  # UC2 + all three weapon counters
    "UF3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,cookoff,shortfirst,triangle,dropmortar",  # UF2 + drop our mortar near enemy lasers
    "UE1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,cookoff",  # UC2 + laser cook-off targeting of mortar carriers
    "UE2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,shortfirst",  # UC2 + lightning first on mechs charging a laser
    "UE3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,triangle",  # UC2 + weapon chosen to counter the target's weapons
    "UE4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,dropmortar",  # UC2 + drop our mortar near enemy lasers
    "UD1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,lpeek",  # UC2 + laser corner peek
    "UD2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,safedrop",  # UC2 + safe supply drops
    "UD3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,lightrest",  # UC2 + lightning energy rest
    "UD4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback,nodrone",  # UC2 + no drone farming
    "UC1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,safedrop",  # DUAL + safe supply drops
    "UC2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,droneback",  # DUAL + drone retaliation
    "UC3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,lightrest",  # DUAL + lightning energy rest
    "UC4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual",  # DUAL + focus fire
    "UC5": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,reloadduck",  # DUAL + duck out of sight while reloading
    "UC6": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,odds",  # DUAL + fight-odds model
    "UB1": "gunwise,precharge,laserfirst,upg3,reloadwait,finish,dodge,holdspot,midcube,thirdparty,poke,skilladapt,repair,memory,travelboost,crowdrange,edgepatrol,roofesc,hearlaser,dual,odds,outgunned,reloadduck,droneback,safedrop,lightrest",  # owner's builder request (20 picked + 8 new behaviours)
    "UA1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,berserk,edgepatrol,roofesc,roofloot,laserfirst,lethal",  # owner's design: aggressive, re-heal, edge patrol, rooftops, smart weapons
    "UA2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,berserk,edgepatrol,laserfirst,lethal",  # UA1 without rooftops
    "AP5": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual",  # DUAL + stronger low-HP and sticky target ranking
    "AO1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,nodrone",  # DUAL + no drone farming
    "AO2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,hunterwatch",  # DUAL + leave passive house bots alone
    "AO3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,stayput",  # DUAL + stay put when idle
    "AO4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,nodrone,hunterwatch,stayput",  # DUAL + AO1 + AO2 + AO3
    "AO5": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,nodrone,hunterwatch,stayput",  # AO4 + travel boost from 25 energy
    "AO6": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,nodrone,hunterwatch",  # DUAL + no drone farming + leave passive bots alone
    "AN1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AC3, commit to won fights at 40% / 40%
    "AN2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AC3, travel boost from 25 energy
    "AN3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AC3, 10 s repair pause after a kill
    "AN4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AN1 + AN2
    "AM1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,strafe2,nodrone",  # AL3 + no drone farming
    "AM2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,strafe2,dual",  # AL3 + MUSHIN-DUAL free-for-all brain
    "AM3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,strafe2",  # AL3 with a 1.6 s strafe rhythm
    "AM4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,strafe2",  # AL3 with a 3.0 s strafe rhythm
    "AL1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,crowdrange",  # AC3 + keep 40 m further out against a crowd or when hurt
    "AL2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,nodrone",  # AC3 + stop farming NPC drones
    "AL3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,strafe2",  # AC3 + steady 2.2 s strafe rhythm
    "AL4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lethal,treefire,memfire,crowdrange,nodrone,strafe2",  # AG4 + AL1 + AL2 + AL3
    "AK1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,stayput",  # AC3 + hold ground instead of trekking to a far hide spot
    "AK2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,stayput",  # AC3 + always hold ground unless the zone moves us
    "AK3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,upg3",  # AC3 + laser/lightning-first upgrade order
    "AK4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lethal,treefire,memfire,stayput,upg3",  # AG4 + AK1 + AK3
    "AJ1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,skilladapt",  # AC3 + adapt to each house bot's measured skill
    "AJ2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,hunterwatch",  # AC3 + leave bystander house bots alone
    "AJ3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,skilladapt,hunterwatch",  # AJ1 + AJ2
    "AJ4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lethal,treefire,memfire,skilladapt,hunterwatch",  # AG4 + AJ3
    "AI1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AC3 + head for the next circle 45 s early
    "AI2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AC3 + head for the next circle 60 s early
    "AI3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AC3 + hide deeper inside the next circle
    "AI4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AI2 + AI3
    "AH1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,canopyrest",  # AC3 + heal under tree cover
    "AH2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lateheal",  # AC3 + late game: heal before opening new fights
    "AH3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,canopyrest,lateheal",  # AH1 + AH2
    "AH4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lethal,treefire,memfire,canopyrest,lateheal",  # AG4 + AH3
    "AG1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lethal",  # AC3 + release a partial laser beam once it kills
    "AG2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,treefire",  # AC3 + gun fire at heard mechs hidden in trees
    "AG3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,memfire",  # AC3 + keep firing where a just-lost target should be
    "AG4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lethal,treefire,memfire",  # AG1 + AG2 + AG3
    "AF5": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lpeek",  # AC3 + laser corner peek
    "AF6": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,lpeek,poke",  # AC3 + laser corner peek + anti-regen poke
    "AF1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,gunlaser,lpeek",  # AE1 + laser corner peek
    "AF2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,gunlaser,lpeek",  # AE1 + laser corner peek, 80 m and beyond
    "AF3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,gunlaser,poke",  # AE1 + anti-regen poke
    "AF4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,gunlaser,lpeek,poke",  # AF1 + AF3
    "AE1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,gunlaser",  # AC3 + gun fires with lead while the laser charges
    "AE2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,cubecap",  # AC3 + stop chasing cubes past 10
    "AE3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,gunlaser,cubecap",  # AE1 + AE2
    "AE4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual,gunlaser",  # MUSHIN-DUAL + gun fires while the laser charges
    "DUAL": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,dual",  # AC3 in practice, MUSHIN-UI v2 once a free-for-all is detected
    "AD1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,pushhold",  # T2 + hold the corner when an enemy pushes us
    "AD2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,flank",  # T2 + flank: prefer enemies whose back is to us
    "AD3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,thirdparty,pushhold",  # AC4 + hold the corner when pushed
    "AD4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,pushhold",  # AC1 + hold the corner when pushed
    "AC1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish",  # T2 + commit to fights already won
    "AC2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,travelboost",  # T2 + boost across open ground when no enemy is in sight
    "AC3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost",  # AC1 + AC2
    "AC4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,finish,travelboost,thirdparty",  # AC3 + third party (AB3)
    "AB1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,thirdparty",  # T2 + third party: prefer busy enemies, shoot from 50-110 m
    "AB2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,thirdparty",  # T2 + third party, 35-80 m
    "AB3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,thirdparty",  # T2 + third party, strong preference for busy enemies
    "AB4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,thirdparty",  # T2 + third party, 70-140 m
    "Z1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,fleespot",  # T2 + committed flee spot
    "Z2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,hearlaser",  # T2 + react to laser-charge sounds
    "Z3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,backstab",  # T2 + unseen attackers count when picking cover
    "Z4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,fleespot,hearlaser,backstab",  # Z1 + Z2 + Z3
    "Y1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 + gun band 30-70 m
    "Y2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 + gun band 25-85 m
    "Y3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 + gun band 40-100 m
    "Y4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 + gun band 30-85 m
    "W1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 + closer gun band (20-55 m)
    "W2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 + farther gun band (35-85 m)
    "W3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 + fight longer before fleeing
    "W4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 + flee earlier
    "V1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,lowammo",  # T2 + also hold when the gun is nearly empty
    "V2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,fightreload",  # T2 + reload behind cover mid-fight
    "V3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,lowammo,fightreload",  # V1 + V2
    "V4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,lowammo,fightreload,roofesc",  # V3 + roof escape
    "U1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 with cover range 1.1x
    "U2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 with cover range 1.0x
    "U3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,roofesc",  # T2 + roof escape
    "U4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # T2 with repair at 65%
    "T1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,reloadback",  # R3 + back off while the gun reloads
    "T2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # R3 with cover range 1.2x
    "T3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,roofesc",  # R3 + roof escape
    "T4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait,reloadback",  # T1 with a 25 s repair window
    "S1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # Q2 with cover range 1.2x
    "S2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # Q2 with cover range 1.4x
    "S3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,roofesc",  # Q2 + roof escape
    "S4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # Q2 with a 25 s repair window after kills
    "R1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # P1 + fight from cover out to 1.5x range
    "R2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # P1 + fight from cover out to 1.8x range
    "R3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # Q2 + no pushing while the gun reloads
    "R4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # Q2 + crates and cubes out to 150 m
    "Q1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # P1 with repair at 80%, 100%, 25 s
    "Q2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # P1 + fight from cover out to 1.3x range
    "Q3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,sprint",  # P1 + fast opening sweep
    "Q4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # P1 + no pushing while the gun reloads
    "P1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # O1 with repair at 75%, 100%, 15 s
    "P2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,roofesc",  # O1 + roof escape
    "P3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,upg2",  # repair at 70%, 100%, 12 s + crates, cubes, upgrade order
    "P4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # O1 with repair at 85%, 100%, 20 s
    "O1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # M1 with even stronger repair (65%, 100%, 12 s)
    "O2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,upg2",  # M1 + crates 150 m, cubes 150 m, upgrade order
    "O3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,sprint",  # M1 + fast opening sweep
    "O4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,upg2",  # O1 + O2
    "N1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # K4 + break ground crates out to 150 m
    "N2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # K4 + stronger cube priority (150 m)
    "N3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,upg2",  # K4 + kanyaraasi-style upgrade order
    "N4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,upg2",  # N1 + N2 + N3
    "M1": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # K4 with stronger repair (60%, 95%, 10 s)
    "M2": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,sprint",  # K4 + fast opening sweep
    "M3": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,reloadwait",  # K4 + no pushing while the gun reloads
    "M4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory,roofesc",  # K4 + roof escape
    "L1": "gunwise,dodge,holdspot,repair,memory,midcube",     # I3 + mid-fight grabs
    "L2": "gunwise,dodge,holdspot,repair,memory,reloadwait",  # I3 + don't push while reloading
    "L3": "gunwise,dodge,holdspot,repair,memory,sprint",      # I3 + fast opening sweep
    "L4": "gunwise,dodge,holdspot,repair,memory",             # I3 with stronger repair (60% / 95% / 10 s)
    "K1": "gunwise,dodge,holdspot,midcube",                          # G4 + grab cubes / a special mid-fight
    "K2": "gunwise,dodge,holdspot,precharge",                        # G4 + laser pre-charge on tracked targets
    "K3": "gunwise,dodge,holdspot,midcube,precharge",                # K1 + K2
    "K4": "gunwise,dodge,holdspot,midcube,precharge,repair,memory",  # K3 + I3 (repair + loot memory)
    "J1": "gunwise,dodge,holdspot,sprint",           # G4 + fast opening loot sweep
    "J2": "gunwise,dodge,holdspot",                  # G4 + close gun brawler: gun 20-40 m, retreat at 15% HP
    "J3": "gunwise,dodge,holdspot,repair,sprint",    # I1 + fast opening
    "J4": "gunwise,dodge,holdspot,memory,sprint",    # I2 + fast opening
    "I1": "gunwise,dodge,holdspot,repair",                 # G4 + heal quietly between fights, finish weak targets
    "I2": "gunwise,dodge,holdspot,memory",                 # G4 + remember and farm loot / cubes / drones / crates
    "I3": "gunwise,dodge,holdspot,repair,memory",          # I1 + I2
    "I4": "gunwise,dodge,holdspot,repair,memory,roofesc",  # I3 + roof escape
    "H1": "gunwise,dodge,holdspot,roofesc",    # G4 + jet onto a low roof to escape when hurt
    "H2": "gunwise,dodge,holdspot",            # G4 + head for the next circle 60 s early
    "H3": "gunwise,dodge,holdspot,roofloot",   # G4 + climb for nearby roof loot when safe
    "H4": "gunwise,dodge,holdspot",            # G4, hold the spot for targets up to 40% beyond our range
    "G7": "gunwise,dodge,laserfirst,holdspot",  # G1 (best real 3.92) + G4: laser first, fight from the pre-chosen spot
    "F3": "gunwise,dodge,berserk,laserfirst",  # F1 + grab a laser/lightning in sight before chasing mechs not shooting us
    "D1": "gunwise",            # A9, fight harder: retreat only when nearly dead or swarmed
    "D2": "gunwise,econ",       # A9 + economy loop: farm wider, opportunistic shots at crates and drones
    "X10": "gunwise,econ,harvest,easy,heal,crowd,calmlate",  # profile: loot first, easy kills, heal at 40% (65% late), avoid being focused, calm late game
    "X11": "gunwise,econ,harvest,easy,heal,crowd,calmlate,dodge",  # X10 + dodge
    "X12": "gunwise,econ,harvest,heal,crowd,calmlate",  # X10 without easy-kills-only
    "X13": "gunwise,econ,easy,heal,crowd,calmlate",  # X10 without the harvest phase
    "X14": "gunwise,econ,harvest,easy,crowd,calmlate",  # X10 without healing
    "X15": "gunwise,econ,harvest,easy,heal,calmlate",  # X10 without the crowd rule
    "X16": "gunwise,econ,harvest,easy,heal,crowd",  # X10 without the calm late game
    "X17": "gunwise,econ,dodge",  # D2 + dodge
    "X18": "gunwise,econ,heal",  # D2 + heal (40%, 65% late)
    "X19": "gunwise,econ,crowd",  # D2 + crowd rule
    "X20": "gunwise,econ,heal,crowd",  # D2 + heal + crowd
    "X21": "gunwise,econ,heal,crowd,dodge",  # X20 + dodge
    "X22": "gunwise,econ,heal,crowd,calmlate",  # X20 + calm late game
    "X23": "gunwise,econ,heal,crowd",  # X20, heal from 30%
    "X24": "gunwise,econ,heal,crowd",  # X20, no late-game heal raise
    "X25": "gunwise,econ,heal,crowd",  # X20, shorter heal (to 75%)
    "X26": "gunwise,econ,heal,crowd,harvest",  # X20 + harvest phase
    "X27": "gunwise,econ,crowd,dodge",  # D2 + crowd + dodge
}
# Per-version setting overrides, applied on top of PARAMS (SURVIVOR_PARAMS still overrides both).
VERSION_PARAMS = {
    "B4": {"gun_maxd_early": 80, "gun_maxd_mid": 130, "gun_maxd_late": 180,
           "gun_phit_early": 0.55, "gun_phit_mid": 0.40, "gun_phit_late": 0.25},
    "B5": {"gun_maxd_early": 150, "gun_maxd_mid": 210, "gun_maxd_late": 230,
           "gun_phit_early": 0.30, "gun_phit_mid": 0.18, "gun_phit_late": 0.05},
    "B6": {"rng_light_lo": 8.486, "rng_light_hi": 18.548, "rng_gun_hi": 173.441, "flee_hp2": 0.8, "flee_ratio2": 1.216,
           "flee_nlos": 4.0, "flee_hold": 1.949, "flee_boost_e": 31.297, "strafe_min": 0.43, "ts_hp": 0.009,
           "zone_edge": 9.065, "zone_margin": 33.745, "zone_margin_frac": 0.47, "hide_cover": 24.823,
           "hide_center": 4.326, "loot_w_range": 106.216, "loot_w_rank": 18.538, "loot_c_bonus": 4.605,
           "rank_laser": 3.006, "rank_mortar": 0.346},
    # B7: from the mid stage on, keep HP: flee earlier, don't chase; supply-drop and cube reach in loot_goal
    "B7": {"end_flee_hp1": 0.5, "end_flee_hp2": 0.7},
}
VERSION_PARAMS.update({"C1": VERSION_PARAMS["B4"], "C2": VERSION_PARAMS["B5"], "C3": VERSION_PARAMS["B6"], "C4": VERSION_PARAMS["B7"],
                       "C6": {"flee_nlos": 2}, "C9": {"flee_nlos": 2}, "D1": {"flee_hp1": 0.2, "flee_hp2": 0.3, "flee_natt": 3, "flee_nlos": 4}, "D2": {"crate_range": 150, "loot_c_range": 110}, "E2": VERSION_PARAMS["B6"], "G2": {"zone_lead": 60}, "H2": {"zone_lead": 60}, "L4": {"rep_lo": 0.6, "rep_hi": 0.95, "rep_kill": 10}, "M1": {"rep_lo": 0.6, "rep_hi": 0.95, "rep_kill": 10}, "UL1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UK1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UK2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UJ1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UI1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UI2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UH1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UH2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UG1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UG2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UG3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UG4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UF2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UF3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UF1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UE1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UE2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UE3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UE4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UD1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 45}, "UD2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 45}, "UD3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 45}, "UD4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 45}, "UC1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UC2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UC3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UC4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "ts_sticky": 1000}, "UC5": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UC6": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "UB1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110, "ep_in": 25, "flee_hold": 1.5, "ts_sticky": 1000}, "UA1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "bz_flee": 0.3, "bz_back": 0.8, "radial": 1.0, "fight_boost_e": 30, "fight_boost_d": 250, "ep_in": 25}, "UA2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "bz_flee": 0.3, "bz_back": 0.8, "radial": 1.0, "fight_boost_e": 30, "fight_boost_d": 250, "ep_in": 25}, "AP5": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "ts_hp": 0.18, "ts_att": 40, "ts_sticky": 200, "ts_low": 200}, "AO1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 60}, "AO2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 60}, "AO3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 60}, "AO4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 60}, "AO5": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 60, "tb_e": 25}, "AO6": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 60}, "AN1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "fin_t": 0.4, "fin_me": 0.4}, "AN2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tb_e": 25}, "AN3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 10, "hold_range": 1.2}, "AN4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "fin_t": 0.4, "fin_me": 0.4, "tb_e": 25}, "AM1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AM2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "AM3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "strafe_T": 1.6}, "AM4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "strafe_T": 3.0}, "AL1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AL2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AL3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AL4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AK1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 60}, "AK2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 0}, "AK3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AK4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "sp_d": 60}, "AJ1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AJ2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AJ3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AJ4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AI1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "zone_lead": 45}, "AI2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "zone_lead": 60}, "AI3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "zone_margin": 45, "zone_margin_frac": 0.5}, "AI4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "zone_lead": 60, "zone_margin": 45, "zone_margin_frac": 0.5}, "AH1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AH2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AH3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AH4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AG1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AG2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AG3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AG4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AF5": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 45}, "AF6": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 45}, "AF1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 45}, "AF2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 80}, "AF3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AF4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "lp_lo": 45}, "AE1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "AE2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "AE3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "AE4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "DUAL": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "AD1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AD2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AD3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "AD4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AC1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AC2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AC3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "AC4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "AB1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 60, "tp_lo": 50, "tp_hi": 110}, "AB2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 60, "tp_lo": 35, "tp_hi": 80}, "AB3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 120, "tp_lo": 50, "tp_hi": 110}, "AB4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "tp_bonus": 60, "tp_lo": 70, "tp_hi": 140}, "Z1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "Z2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "Z3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "Z4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "Y1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "rng_gun_lo": 30, "rng_gun_hi": 70}, "Y2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "rng_gun_lo": 25, "rng_gun_hi": 85}, "Y3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "rng_gun_lo": 40, "rng_gun_hi": 100}, "Y4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "rng_gun_lo": 30, "rng_gun_hi": 85}, "W1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "rng_gun_lo": 20, "rng_gun_hi": 55}, "W2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "rng_gun_lo": 35, "rng_gun_hi": 85}, "W3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "flee_hp1": 0.25, "flee_hp2": 0.4}, "W4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2, "flee_hp1": 0.45, "flee_hp2": 0.6}, "V1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "V2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "V3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "V4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "U1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.1}, "U2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.0}, "U3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "U4": {"rep_lo": 0.65, "rep_hi": 1.0, "rep_kill": 12, "hold_range": 1.2}, "T1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.3}, "T2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "T3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.3}, "T4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 25, "hold_range": 1.3}, "S1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.2}, "S2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.4}, "S3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.3}, "S4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 25, "hold_range": 1.3}, "R1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.5}, "R2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.8}, "R3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.3}, "R4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.3, "crate_range": 150, "crate_hp": 0.45, "loot_c_range": 150, "loot_c_bonus": 30}, "Q1": {"rep_lo": 0.8, "rep_hi": 1.0, "rep_kill": 25}, "Q2": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15, "hold_range": 1.3}, "Q3": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15}, "Q4": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15}, "P1": {"rep_lo": 0.75, "rep_hi": 1.0, "rep_kill": 15}, "P2": {"rep_lo": 0.65, "rep_hi": 1.0, "rep_kill": 12}, "P3": {"rep_lo": 0.7, "rep_hi": 1.0, "rep_kill": 12, "crate_range": 150, "crate_hp": 0.45, "loot_c_range": 150, "loot_c_bonus": 30}, "P4": {"rep_lo": 0.85, "rep_hi": 1.0, "rep_kill": 20}, "O1": {"rep_lo": 0.65, "rep_hi": 1.0, "rep_kill": 12}, "O2": {"rep_lo": 0.6, "rep_hi": 0.95, "rep_kill": 10, "crate_range": 150, "crate_hp": 0.45, "loot_c_range": 150, "loot_c_bonus": 30}, "O3": {"rep_lo": 0.6, "rep_hi": 0.95, "rep_kill": 10}, "O4": {"rep_lo": 0.65, "rep_hi": 1.0, "rep_kill": 12, "crate_range": 150, "crate_hp": 0.45, "loot_c_range": 150, "loot_c_bonus": 30}, "N1": {"crate_range": 150, "crate_hp": 0.45}, "N2": {"loot_c_range": 150, "loot_c_bonus": 30}, "N4": {"crate_range": 150, "crate_hp": 0.45, "loot_c_range": 150, "loot_c_bonus": 30}, "J2": {"rng_gun_lo": 20, "rng_gun_hi": 40, "flee_hp1": 0.15, "flee_hp2": 0.2, "ts_nolos": 20}, "H4": {"hold_range": 1.4}, "G3": {"zone_lead": 60}, "F1": {"radial": 1.0, "fight_boost_e": 30, "fight_boost_d": 250, "bz_flee": 0.25, "bz_back": 0.5}, "F2": {"radial": 1.0, "fight_boost_e": 30, "fight_boost_d": 250, "bz_flee": 0.35, "bz_back": 0.6}, "F3": {"radial": 1.0, "fight_boost_e": 30, "fight_boost_d": 250, "bz_flee": 0.25, "bz_back": 0.5}, "X20": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X21": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X22": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X23": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.3, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X24": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.4}, "X25": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.75, "heal_lo_late": 0.65}, "X26": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X27": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X10": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X11": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X12": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X13": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X14": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X15": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X16": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X17": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X18": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "X19": {"crate_range": 150, "loot_c_range": 110, "heal_lo": 0.4, "heal_hi": 0.9, "heal_lo_late": 0.65}, "C7": {"rng_laser_lo": 120, "rng_laser_hi": 150}})
DEFAULT_VERSION = "UL1"


def load_params(version=None):
    p = dict(PARAMS)
    p.update({"end_flee_hp1": 0.5, "end_flee_hp2": 0.7})
    for k, v in VERSION_PARAMS.get(version, {}).items():
        p[k] = float(v)
    raw = os.environ.get("SURVIVOR_PARAMS")
    if raw:
        try:
            import json
            for k, v in json.loads(raw).items():
                if k in p:
                    p[k] = float(v)
        except Exception as e:
            print(f"bad SURVIVOR_PARAMS ignored: {e}", file=sys.stderr)
    return p
UPGRADE_PREF = ["plating", "armor", "nanites", "laser_damage", "laser_charge", "gun_damage", "gun_rate",
                "lightning_efficiency", "servos", "gun_mag", "lightning_range", "capacitor", "gun_velocity",
                "reactor", "jets", "gyros", "sensors", "mortar_reload", "mortar_splash", "mortar_velocity",
                "lightning_chain", "laser_pierce"]


UPGRADE_PREF4 = ["sensors", "plating", "armor", "nanites", "laser_damage", "laser_charge", "gun_damage", "gun_rate",
                 "lightning_efficiency", "servos", "gun_mag", "lightning_range", "capacitor", "gun_velocity", "reactor", "jets",
                 "gyros", "mortar_reload", "mortar_splash", "mortar_velocity", "lightning_chain", "laser_pierce"]
UPGRADE_PREF3 = ["laser_damage", "laser_charge", "lightning_efficiency", "lightning_range", "plating", "gun_mag", "servos",
                 "armor", "nanites", "gun_damage", "gun_rate", "gun_velocity", "reactor", "jets", "capacitor", "gyros", "sensors",
                 "lightning_chain", "laser_pierce", "mortar_reload", "mortar_velocity", "mortar_splash"]
UPGRADE_PREF2 = ["plating", "armor", "nanites", "lightning_efficiency", "laser_damage", "laser_charge", "gun_damage",
                 "gun_rate", "lightning_range", "lightning_chain", "gyros", "servos", "gun_mag", "capacitor", "reactor", "jets",
                 "gun_velocity", "sensors", "mortar_reload", "mortar_velocity", "mortar_splash", "laser_pierce"]


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
        self.mu2 = None; self.dual_ffa = False; self.dual_why = ""; self.dual_deaths = []; self.dual_ev = []
        self.dual_hp = {}; self.dual_kills = 0
        self.dual_hello = hello
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
        fl = os.environ.get("SURVIVOR_FLAGS")
        if fl is None:
            self.version = os.environ.get("SURVIVOR_VERSION", DEFAULT_VERSION)
            fl = VERSIONS.get(self.version, "")
        else:
            self.version = "custom"
        self.P = load_params(self.version)
        self.rank = {"laser": self.P["rank_laser"], "lightning": self.P["rank_light"], "mortar": self.P["rank_mortar"], "gun": 0.0}
        self.flags = set(f.strip() for f in fl.split(",") if f.strip())
        self.phase = "center" if "center" in self.flags else "normal"   # B1 opening: center -> transit -> normal
        self.center_spot = None
        self.stage = "early"
        self.healing = False
        self.bz_retreat = False
        self.climb = None   # (x, y, z, until, why)
        self.repair_until = -99.0
        self.last_threat = None   # (x, y, z, t)
        self.mem = {}   # id -> [kind, pos, t_seen]  (weapons, cubes, npcs, crates)
        self.dmg_out = 0.0; self.dmg_in = 0.0          # G5: running damage totals vs mechs
        self.arm_stats = {"press": [1.0, 0.3], "space": [1.0, 0.0]}   # [pseudo-count, reward sum]: lean to press
        self.eng = None
        # B2/B3: is this a free-for-all? Practice house bots never kill each other, so any death we didn't cause
        # (zone deaths aside) means other mechs are fighting each other.
        self.base_flags = set(self.flags)
        self.canopy_g = self.geo._canopy(hello["map"]["dir"]) if "canopyrest" in self.flags else None
        self.canopy_spot = None; self.canopy_t = -99.0
        if "dual" in self.flags:
            import mu2bot
            t1 = time.time()
            rs = random.getstate()   # MU2 reseeds the shared random module; keep AC4's sequence unchanged
            self.mu2 = mu2bot.Bot(self.dual_hello)   # hello allows 20 s; both brains are ready before the first tick
            random.setstate(rs)
            mf.log(f"dual: mu2 brain ready in {time.time() - t1:.2f}s")
        self.my_kills = 0
        self.other_deaths = []   # times at which mechs died by other hands (for the timing-aware rule)
        self.ffa = False
        self.plan = "hunt" if "switch" in self.flags else "build"
        self._cover_cache = None
        self.contact_t = -99.0   # last time an enemy was seen within 70 m or heard near
        self.threat_bearing = None
        self.threat_until = 0.0
        self.attackers = {}      # id -> last time they damaged us
        self.sound_bearing = None
        self.sound_until = 0.0
        self.flee_spot = None; self.flee_spot_t = -99.0
        self.lp_on = False; self.lp_peek = None; self.last_hit_on = {}
        self.g_strafe = 1; self.g_strafe_until = 0.0
        self.seen_mech = {}; self.mech_snd = None
        self.dmg_from = {}; self.expo = {}; self.close_ema = {}
        self.laser_snd = None; self.laser_snd_until = 0.0
        self.bs_bearing = None; self.bs_until = 0.0
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
        P = self.P
        margin = min(P["zone_margin"], nr * P["zone_margin_frac"]) + 4
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
        score = g.cover * P["hide_cover"] * (2.0 if "ambush" in self.flags else 1.0) - np.where(np.isfinite(df), df, 1e6) * P["hide_dist"] - dc * P["hide_center"]
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
        if "dual" not in self.flags:
            return self.act_core(obs)
        if self.dual_ffa:
            return self.mu2.act(obs)
        a = self.act_core(obs)
        self.dual_detect(obs)
        if self.dual_ffa:
            self.mu2.attackers.update(self.attackers)
            self.mu2.blacklist.update(self.blacklist)
            mf.log(f"dual: free-for-all at t={obs['t']:.1f} ({self.dual_why})")
        return a

    def dual_detect(self, obs):
        t = obs["t"]
        me = obs["self"]
        hit_by_us = set()
        for e in obs.get("events", []):
            if e["type"] == "kill":
                self.dual_kills += 1
            elif e["type"] == "hit":
                hit_by_us.add(e.get("target"))
        total = self.cfg.get("match", {}).get("mechs", 16)
        others = (total - obs.get("alive", total)) - self.dual_kills
        while len(self.dual_deaths) < others:
            self.dual_deaths.append(t)
        n = len(self.dual_deaths)
        if n and ((n >= 2 and t < 30) or (n >= 3 and t < 60) or n >= 6):
            self.dual_ffa = True; self.dual_why = f"{n} deaths we did not cause"
            return
        if t > 45:
            return   # fights between visible mechs only count early: later, zone and drone damage look the same
        mechs = [m for m in obs.get("mechs", []) if m.get("kind", "mech") == "mech"]
        mx, my = me["pos"][0], me["pos"][1]
        hp_now = {m["id"]: m["hp"] for m in mechs if m.get("hp") is not None}
        for a in mechs:
            if not a.get("aim"):
                continue
            ax, ay = a["pos"][0], a["pos"][1]
            if abs(wrap(a["aim"][0] - math.atan2(my - ay, mx - ax))) < 0.6:
                continue   # it may be aiming at us
            for b in mechs:
                if b is a:
                    continue
                dx, dy = b["pos"][0] - ax, b["pos"][1] - ay
                if dx * dx + dy * dy > 140 * 140 or abs(wrap(a["aim"][0] - math.atan2(dy, dx))) > 0.35:
                    continue
                if not b.get("aim") or abs(wrap(b["aim"][0] - math.atan2(-dy, -dx))) > 0.35:
                    continue   # house bots never aim at each other; a real fight is mutual
                lost = b["id"] in self.dual_hp and b["id"] in hp_now and hp_now[b["id"]] < self.dual_hp[b["id"]] - 1 \
                    and b["id"] not in hit_by_us
                if lost:
                    self.dual_ev.append(t)
        self.dual_hp = hp_now
        self.dual_ev = [u for u in self.dual_ev if t - u < 3.0]
        if len(self.dual_ev) >= 2:
            self.dual_ffa = True; self.dual_why = "mechs fighting each other"

    def act_core(self, obs):
        t = obs["t"]
        self.t_now = t
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
            pref = UPGRADE_PREF4 if "upg4" in self.flags else UPGRADE_PREF3 if "upg3" in self.flags else (UPGRADE_PREF2 if "upg2" in self.flags else UPGRADE_PREF)
            best = min(range(len(ch)), key=lambda k: pref.index(ch[k]["id"]) if ch[k]["id"] in pref else 99)
            act["pick"] = best

        # ---- events
        for e in obs.get("events", []):
            if e["type"] == "damaged" and e.get("bearing") is not None and e.get("weapon") != "zone":
                self.threat_bearing = e["bearing"]
                self.threat_until = t + 2.5
                if e.get("by") is not None:
                    self.attackers[e["by"]] = t
                    self.dmg_from[e["by"]] = self.dmg_from.get(e["by"], 0.0) + e.get("amount", 0.0)
                    if e.get("weapon") == "npc":
                        self.npc_hit = (e["by"], t)
                if e.get("rear") or e.get("by") not in {m["id"] for m in obs.get("mechs", [])}:
                    self.bs_bearing = e["bearing"]; self.bs_until = t + 0.65
            elif e["type"] == "kill":
                self.my_kills += 1
                self.seen_mech.pop(e.get("target"), None)
                self.repair_until = max(self.repair_until, t + self.P["rep_kill"])
            elif e["type"] == "take_failed":
                self.blacklist[e["id"]] = t + 20
            elif e["type"] == "hit" and isinstance(e.get("target"), int):
                self.last_hit_on[e["target"]] = t
        for s in obs.get("sounds", []):
            if s["kind"] in ("gun", "laser", "laser_charge", "lightning", "mortar", "step", "boost", "jet", "reload") and s["dist"] in ("near", "mid"):
                if t > self.sound_until or s["dist"] == "near":
                    self.sound_bearing = s["bearing"]
                    self.sound_until = t + 1.5
            if s["kind"] == "laser_charge" and s["dist"] in ("near", "mid"):
                self.laser_snd = s["bearing"]; self.laser_snd_until = t + 0.75
            if s["kind"] in ("step", "gun", "boost", "jet", "reload", "laser_charge") and s["dist"] in ("near", "mid") \
                    and s.get("age", 0) < 0.3:
                self.mech_snd = (s["bearing"], t)

        for e in obs.get("events", []):
            if e["type"] == "hit" and isinstance(e.get("target"), int) and e["target"] < 100:
                self.dmg_out += e.get("amount", 0)
            elif e["type"] == "damaged" and e.get("weapon") != "zone":
                self.dmg_in += e.get("amount", 0)

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

        if "phased" in self.base_flags:
            self.update_plan(obs, me, kinds, t)

        # ---- zone
        cur_c, cur_r, nc, nr, ss, se = self.zone_info(obs)
        dcur = d2((x, y), cur_c)
        in_zone = me["in_zone"]
        dnext = d2((x, y), nc)
        P = self.P
        margin = min(P["zone_margin"], nr * P["zone_margin_frac"])
        need_zone = (not in_zone) or (dnext > nr - margin * 0.3 and (t > ss - P["zone_lead"] or dnext > nr + 60)) or (dcur > cur_r - P["zone_edge"])

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
        # which way each mech faces relative to us: off = angle between its torso aim and the line from it to us.
        # off < ~60 deg: we're in its view wedge; off > 90 deg: we're behind its front arc (our hits do rear damage)
        arc = math.radians(self.mech.get("front_arc_deg", 180)) / 2
        for m in mechs:
            to_us = math.atan2(y - m["pos"][1], x - m["pos"][0])
            m["off"] = abs(wrap(m["aim"][0] - to_us)) if m.get("aim") else math.pi / 2
            if m["los"] and m["off"] < 0.6:
                self.expo[m["id"]] = self.expo.get(m["id"], 0.0) + 0.1
            if m.get("vel"):
                vc = m["vel"][0] * math.cos(to_us) + m["vel"][1] * math.sin(to_us)
                self.close_ema[m["id"]] = 0.9 * self.close_ema.get(m["id"], vc) + 0.1 * vc
            if "memfire" in self.flags:
                self.seen_mech[m["id"]] = (t, list(m["pos"]), list(m.get("vel") or [0, 0, 0]))
            m["back"] = m["off"] > arc + 0.15
            m["faces"] = m["off"] < math.radians(self.vis.get("fov_deg", 120)) / 2
        for m in mechs:
            m["busy"] = False
            if "thirdparty" in self.flags and m.get("aim") and m["off"] > 0.6:
                for o in mechs:
                    if o is m:
                        continue
                    dx, dy = o["pos"][0] - m["pos"][0], o["pos"][1] - m["pos"][1]
                    if dx * dx + dy * dy < 160 * 160 and abs(wrap(m["aim"][0] - math.atan2(dy, dx))) < 0.35:
                        m["busy"] = True
                        break
        self.stage = "early" if cur_r > P["stage_mid_r"] else ("late" if cur_r < P["stage_late_r"] else "mid")
        if mechs:
            nm = min(mechs, key=lambda m: m["d"]); self.last_threat = (nm["pos"][0], nm["pos"][1], nm["pos"][2], t)
        if "memory" in self.flags:
            self.remember(obs, npcs, x, y, t)

        def threat_score(m):
            s = m["d"] + m["hp"] * P["ts_hp"]
            if m["id"] in self.attackers and t - self.attackers[m["id"]] < 4:
                s -= P["ts_att"]
            if not m["los"]:
                s += P["ts_nolos"]
            if m.get("busy"):
                s -= P["tp_bonus"]
            if m["id"] == self.cur_target and t - self.cur_target_t < 3:
                s -= P["ts_sticky"]
            if P["ts_low"] and m.get("hp") is not None and m["hp"] <= 0.3 * m.get("max_hp", 1400):
                s -= P["ts_low"]
            if "cookoff" in self.flags and "mortar" in m.get("weapons", []) \
                    and any(w and w["kind"] == "laser" and w["cooldown"] < 0.5 for w in weapons):
                s -= 60   # UE1: cook-off: +60% laser damage on mortar carriers
            if "chainaim" in self.flags and "lightning" in kinds and m["d"] < 50 \
                    and any(o is not m and math.dist(o["pos"], m["pos"]) < 14 for o in mechs):
                s -= 40   # UG2: the arc chains to the neighbour
            if "mortarlight" in self.flags and "lightning" in m.get("weapons", []) \
                    and any(w and w["kind"] == "mortar" and w["cooldown"] < 0.5 for w in weapons):
                s -= 40   # UG4: concussion drains the energy lightning needs
            if "shortfirst" in self.flags and m.get("charging") and m["d"] < 45 and "lightning" in kinds:
                s -= 150   # UE2: short its laser before it fires
            if "hunterwatch" in self.flags and m["d"] > 60 and self.close_ema.get(m["id"], 0.0) < 1.0 and m["off"] > 0.8 \
                    and not (m["id"] in self.attackers and t - self.attackers[m["id"]] < 8):
                s += 60   # AJ2: a bystander, not hunting us: leave it be
            if "flank" in self.flags or "ambush" in self.flags:
                if m["back"]:
                    s -= 70   # prefer backs
                elif m["faces"] and m["los"]:
                    s += 50
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

        def hit_us(m, within):
            return m["id"] in self.attackers and t - self.attackers[m["id"]] < within

        kitted = all(kinds) and any(k and k != "gun" for k in kinds) and me.get("cubes", 0) >= P["harvest_cubes"]
        harvesting = "harvest" in self.flags and self.stage == "early" and not kitted
        selective = bool(self.flags & {"pick", "flank", "ambush", "easy"}) or harvesting
        if "flank" in self.flags:
            # A7: go for any back we can shoot; anyone else only if it is already shooting us or is point blank
            cands = [m for m in mechs if hit_us(m, 4) or (m["los"] and (m["back"] or m["d"] < 35))]
        elif "ambush" in self.flags:
            # A8: only a lone mech with its back to us, and only while nobody else has a line on us; defend if hit
            seen_us = [m for m in mechs if m["los"]]

            def alone(m):
                return all(math.dist(o["pos"][:2], m["pos"][:2]) > 70 for o in mechs if o is not m)
            cands = [m for m in mechs if len(seen_us) <= 1 and ((m["los"] and m["back"] and alone(m)) or hit_us(m, 3))]
        elif "easy" in self.flags or harvesting:
            def easy(m):
                return hit_us(m, 4) or m["d"] < P["easy_d"] or (m["los"] and (m["hp"] < P["easy_hp"] * m.get("max_hp", 1400) or m["back"]))
            cands = [m for m in mechs if easy(m)]
        elif "pick" in self.flags:
            cands = [m for m in mechs if threatening(m)]
        else:
            cands = mechs
        if "laserfirst" in self.flags and not any(k in ("laser", "lightning") for k in kinds):
            spec = [p for p in obs.get("pickups", []) if p["kind"] in ("laser", "lightning")
                    and d2(p["pos"], (x, y)) < 120 and self.blacklist.get(p["id"], 0) <= t]
            if spec:
                cands = [m for m in cands if hit_us(m, 4) or m["d"] < 30]
                self.want_special = min(spec, key=lambda p: d2(p["pos"], (x, y)))
            else:
                self.want_special = None
        else:
            self.want_special = None
        target = min(cands, key=threat_score) if cands else None
        # a visible mech we chose not to fight: keep our front armour (and eyes) on it and get out of its sight
        watch = None
        if target is None and selective:
            vis = [m for m in mechs if m["los"] and m["d"] < 180]
            watch = min(vis, key=lambda m: m["d"]) if vis else None
        if target is not None:
            self.cur_target = target["id"]; self.cur_target_t = t
        if "adapt" in self.flags:
            if target is not None:
                if self.eng is None:
                    tot = sum(v[0] for v in self.arm_stats.values())
                    def ucb(a):
                        n, sm = self.arm_stats[a]
                        return sm / n + 0.6 * math.sqrt(math.log(tot + 1) / n)
                    self.eng = {"arm": max(self.arm_stats, key=ucb), "o0": self.dmg_out, "i0": self.dmg_in, "last": t}
                else:
                    self.eng["last"] = t
            elif self.eng is not None and t - self.eng["last"] > 3:
                r = ((self.dmg_out - self.eng["o0"]) - (self.dmg_in - self.eng["i0"])) / 300.0
                a = self.arm_stats[self.eng["arm"]]; a[0] += 1; a[1] += max(-3.0, min(3.0, r))
                self.eng = None
        recent_att = [k for k, tt in self.attackers.items() if t - tt < 3]
        close_enemies = [m for m in mechs if m["d"] < 120]

        if "heal" in self.flags:
            if hp_frac < (P["heal_lo_late"] if self.stage == "late" else P["heal_lo"]):
                self.healing = True
            elif hp_frac >= P["heal_hi"]:
                self.healing = False

        # ---- choose movement
        crept = False
        tag = ""
        move = (0.0, 0.0)
        boost = False
        mode = "idle"
        aim = None
        shoot_target = None  # (pos, vel, kind, radius, height)

        best_rank = max(self.rank.get(k, -1) for k in kinds if k)

        if target is not None:
            mode = "fight"
            shoot_target = target
            # desired range depends on loadout
            has = set(k for k in kinds if k)
            if "laser" in has:
                want_lo, want_hi = P["rng_laser_lo"], P["rng_laser_hi"]
            elif "lightning" in has:
                want_lo, want_hi = P["rng_light_lo"], P["rng_light_hi"]
            elif "mortar" in has:
                want_lo, want_hi = P["rng_mortar_lo"], P["rng_mortar_hi"]
            else:
                want_lo, want_hi = P["rng_gun_lo"], P["rng_gun_hi"]
            their = set(target.get("weapons", []))
            if "lightning" in their and "lightning" not in has:
                want_lo = max(want_lo, 55); want_hi = max(want_hi, 90)
            if "mortar" in their and "lightning" in has:
                want_hi = min(want_hi, 25)
            n_los = len([m for m in mechs if m["los"] and m["d"] < 160])
            losing = hp_frac < P["flee_hp1"] and (target["hp"] > me["hp"] * P["flee_ratio1"] or n_los > 1)
            if len(recent_att) >= P["flee_natt"] or n_los >= P["flee_nlos"]:
                losing = True
            if hp_frac < P["flee_hp2"] and target["hp"] > me["hp"] * P["flee_ratio2"]:
                losing = True
            bold = shy = False
            if "flank" in self.flags or "ambush" in self.flags:
                if target["back"] and target["los"]:
                    # its back is to us: rear hits do x1.5, so press in and keep shooting unless we're nearly dead
                    bold = True
                    want_lo, want_hi = max(6.0, want_lo * 0.5), max(15.0, want_hi * 0.6)
                    if hp_frac > 0.25 and len(recent_att) < 2:
                        losing = False
                        self.flee_until = 0.0
                elif target["faces"] and target["los"] and target["d"] > 40 and not (hp_frac > 0.7 and target["hp"] < me["hp"] * 0.6):
                    # it's looking at us from range and we don't clearly outgun it: back off into cover, facing it
                    shy = True
                    losing = True
            if "crowdrange" in self.flags and (hp_frac < 0.35 or sum(1 for m in mechs if m["d"] < 100) >= 3):
                want_lo, want_hi = want_lo + 40, want_hi + 40   # AL1: more room against a crowd or when hurt
            if "finish" in self.flags and target["hp"] < P["fin_t"] * target.get("max_hp", target["hp"] + 1) and hp_frac > P["fin_me"]:
                losing = False
                self.flee_until = 0.0
                want_lo, want_hi = want_lo * 0.7, want_hi * 0.7
            if "thirdparty" in self.flags and target.get("busy") and target["los"]:
                want_lo, want_hi = max(want_lo, P["tp_lo"]), max(want_hi, P["tp_hi"])
                if hp_frac > 0.3 and not recent_att:
                    losing = False
                tag += "3"
            if "endgame" in self.flags and self.stage != "early" and not bold:
                # B7: from mid-game, bank HP for the last circles: break off sooner
                if hp_frac < P["end_flee_hp1"] or (hp_frac < P["end_flee_hp2"] and target["hp"] > me["hp"]):
                    losing = True
            if "repair" in self.flags:
                tf = target["hp"] / max(1.0, target.get("max_hp", 1400))
                threats = len([m for m in mechs if m["los"] and m["d"] < 160])
                finishing = tf < 0.2 and hp_frac > 0.2 and threats <= 2
                losing = (not finishing) and ((hp_frac < 0.35 and tf > hp_frac + 0.1) or (threats >= 2 and hp_frac < 0.55)
                                              or (threats >= 3 and hp_frac < 0.8))
                if "twofire" in self.flags:
                    npc_ids = {n["id"] for n in npcs}
                    shooters = sum(1 for aid, ta in self.attackers.items() if t - ta < 2.0 and aid not in npc_ids)
                    if shooters >= 2 and hp_frac < 0.65:
                        losing = True; tag += "2"   # UL: two enemies shooting us and under 65%: get out
                if losing:
                    self.repair_until = max(self.repair_until, t + 6)
                else:
                    self.flee_until = 0.0
            if "berserk" in self.flags:
                if hp_frac < P["bz_flee"]:
                    self.bz_retreat = True
                elif hp_frac >= P["bz_back"]:
                    self.bz_retreat = False
                losing = self.bz_retreat   # numbers or a stronger enemy never make us break off; low HP does
                if not losing:
                    self.flee_until = 0.0
            if "lateheal" in self.flags and obs.get("alive", 16) <= 5 and hp_frac < 0.95 and target["d"] > 45 \
                    and not any(t - tt < 3 for tt in self.attackers.values()):
                losing = True; self.flee_until = 0.0   # AH2: heal up first, let the others fight
            if "crowd" in self.flags and n_los >= 2 and any(t - tt < 3 for tt in self.attackers.values()):
                losing = True   # X10: 2+ enemies have a line on us and we are the one being shot: get out
            if "heal" in self.flags:
                engaged = target["id"] in self.attackers and t - self.attackers[target["id"]] < 3
                if self.healing and not (target["d"] < 30 and engaged):
                    losing = True   # C5: break line of sight and regenerate instead of trading while hurt
                elif hp_frac < P["heal_start"] and not engaged and target["d"] > 45:
                    losing = True   # C5: don't open a new fight below heal_start
            if "odds" in self.flags and target.get("kind", "mech") == "mech":
                th = max(target.get("hp", 1400), 1.0)
                my_w = sum(1 for w in weapons if w); th_w = max(1, len(target.get("weapons", [])))
                shooters = sum(1 for ta in self.attackers.values() if t - ta < 4)
                z = 2.0 * math.log(max(me["hp"], 1.0) / th) + 0.8 * (my_w - th_w) \
                    + 0.5 * (min(me.get("cubes", 0), 10) - min(target.get("cubes", 0), 10)) / 10 - 0.6 * max(0, shooters - 1)
                win = 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))
                if win < 0.3:
                    losing = True   # UB: a duel we expect to lose
                elif win > 0.7 and hp_frac > 0.4:
                    losing = False; self.flee_until = 0.0   # UB: a duel we expect to win
            if "outgunned" in self.flags:
                no_laser = not any(w and w["kind"] == "laser" for w in weapons)
                if (no_laser and "laser" in target.get("weapons", []) and target["d"] > 55 and hp_frac < 0.8) or n_los >= 3:
                    losing = True   # UB: outgunned (laser vs gun at range) or swamped
            if "skilladapt" in self.flags and self.expo.get(target["id"], 0.0) >= 3.0:
                dps = self.dmg_from.get(target["id"], 0.0) / self.expo[target["id"]]
                if dps < 25 and hp_frac > 0.3 and n_los <= 1:
                    losing = False; self.flee_until = 0.0   # AJ1: a weak shooter: keep fighting
                elif dps > 80 and hp_frac < 0.55:
                    losing = True   # AJ1: a strong one: break off earlier
            tag = "!" if bold else ("?" if shy else "")
            if losing:
                self.flee_until = t + P["flee_hold"]
            elif t < self.flee_until:
                losing = True
            tx, ty = target["pos"][0], target["pos"][1]
            bx, by = (tx - x) / max(target["d"], 0.1), (ty - y) / max(target["d"], 0.1)
            if t > self.strafe_until:
                self.strafe_dir = random.choice((-1, 1))
                self.strafe_until = t + random.uniform(P["strafe_min"], max(P["strafe_min"], P["strafe_max"]))
            if "strafe2" in self.flags:
                self.strafe_dir = 1 if int(t / P["strafe_T"]) % 2 == 0 else -1
            px, py = -by * self.strafe_dir, bx * self.strafe_dir
            if losing:
                mode = "flee"
                flee_en = [m for m in mechs if m["d"] < 200] or [target]
                virt = []
                if "backstab" in self.flags and t < self.bs_until and self.bs_bearing is not None:
                    virt.append({"pos": [x + math.cos(self.bs_bearing) * 55, y + math.sin(self.bs_bearing) * 55, z], "d": 55, "id": -11})
                if "hearlaser" in self.flags and t < self.laser_snd_until and self.laser_snd is not None:
                    virt.append({"pos": [x + math.cos(self.laser_snd) * 75, y + math.sin(self.laser_snd) * 75, z], "d": 75, "id": -12})
                spot = None
                if "fleespot" in self.flags and self.flee_spot is not None and t - self.flee_spot_t < 1.8:
                    fx, fy = self.flee_spot
                    if d2((x, y), (fx, fy)) > 4.5 and self.geo.is_free(fx, fy):
                        fz = self.geo.ground(fx, fy) + 4.5
                        chk = sorted(flee_en + virt, key=lambda m: m.get("d", 999))[:4]
                        if all(not self.geo.los((m["pos"][0], m["pos"][1], m["pos"][2] + 8), (fx, fy, fz)) for m in chk):
                            spot = self.flee_spot
                if spot is None:
                    spot = self.cover_from(me, flee_en + virt)
                    if spot is not None:
                        self.flee_spot = spot; self.flee_spot_t = t
                if spot:
                    move, _ = self.nav_to(me, spot[0], spot[1])
                else:
                    move = (-bx + px * 0.5, -by + py * 0.5)
                boost = me["energy"] > P["flee_boost_e"]
            else:
                radial = 0.0
                if target["d"] > want_hi:
                    radial = 0.0 if (("endgame" in self.flags or "calmlate" in self.flags) and self.stage == "late") else P["radial"]   # don't chase late
                elif target["d"] < want_lo:
                    radial = -P["radial"]
                if "reloadwait" in self.flags and radial > 0:
                    gw = next((w for w in weapons if w and w["kind"] == "gun"), None)
                    other_ready = any(w and w["kind"] != "gun" and w["cooldown"] <= 0.2 for w in weapons)
                    if gw and (gw["reload"] > 0 or ("lowammo" in self.flags and gw["ammo"] <= 2)) and not other_ready:
                        radial = -0.5 if "reloadback" in self.flags else 0.0; tag += "r"
                if "pushhold" in self.flags and target.get("vel") and target["d"] < want_hi:
                    closing = -(target["vel"][0] * bx + target["vel"][1] * by)
                    if closing > 3.0:
                        radial = min(radial, 0.0); px, py = px * 0.5, py * 0.5; tag += "c"
                if "adapt" in self.flags and self.eng is not None and self.eng["arm"] == "space":
                    # space: never chase; back off to the far end of our range
                    radial = -P["radial"] if target["d"] < 0.8 * want_hi else 0.0
                    tag += "s"
                if "holdspot" in self.flags and self.hide and d2((x, y), self.hide) < 8 and target["los"] \
                        and target["d"] <= want_hi * P["hold_range"]:
                    radial = 0.0; px, py = px * 0.4, py * 0.4   # at our covered spot with a shot: stay and fight from here
                    tag += "h"
                move = (px + bx * radial, py + by * radial)
                self.lp_on = False
                lw = next((w for w in weapons if w and w["kind"] == "laser"), None)
                if "lpeek" in self.flags and lw is not None and target.get("kind", "mech") == "mech" and P["lp_lo"] <= target["d"] <= 200 \
                        and hp_frac > 0.35 and not need_zone \
                        and not any("lightning" in m.get("weapons", []) and m["d"] < 40 for m in mechs):
                    if target["los"]:
                        self.lp_peek = (x, y)
                    if lw["cooldown"] > 0.55 and lw.get("charge", 0) == 0 and target["los"]:
                        spot = self.cover_from(me, [target])
                        if spot is not None and d2(spot, (x, y)) < 14:
                            move, _ = self.nav_to(me, spot[0], spot[1]); tag += "L-"
                    elif lw["cooldown"] <= 0.55 and not target["los"]:
                        self.lp_on = True
                        if lw.get("charge", 0) >= 0.55 and self.lp_peek is not None:
                            move, _ = self.nav_to(me, self.lp_peek[0], self.lp_peek[1]); tag += "L+"
                        else:
                            move = (0.0, 0.0); tag += "Lc"
                if "poke" in self.flags and not target["los"] and hp_frac > 0.85 and target.get("kind", "mech") == "mech" \
                        and target.get("hp") is not None and target["hp"] < 0.7 * target.get("max_hp", 1400) \
                        and 2.5 <= t - self.last_hit_on.get(target["id"], -99) <= 4.5 and not need_zone:
                    move, _ = self.nav_to(me, target["pos"][0], target["pos"][1]); tag += "p"
                if "reloadduck" in self.flags and target["los"]:
                    gw = next((w for w in weapons if w and w["kind"] == "gun"), None)
                    other = any(w and w["kind"] != "gun" and w["cooldown"] <= 0.3 for w in weapons)
                    if gw is not None and (gw["reload"] > 0 or gw["ammo"] == 0) and not other:
                        spot = self.cover_from(me, [target])
                        if spot is not None and d2(spot, (x, y)) < 12:
                            move, _ = self.nav_to(me, spot[0], spot[1]); tag += "R"   # UB: reload out of sight
                if "cornerfight" in self.flags and target["los"] and target.get("kind", "mech") == "mech":
                    cs = self.corner_spot(me, target, mechs, want_lo, want_hi, t)
                    if cs is not None and d2(cs, (x, y)) > 1.5:
                        move, _ = self.nav_to(me, cs[0], cs[1]); tag += "K"   # UJ: fight next to an escape
                if "fightcover" in self.flags and target["d"] > 40:
                    spot = self.firing_spot(me, target, mechs, want_lo, want_hi, t)
                    if spot is not None:
                        if d2(spot, (x, y)) > 3:
                            move, _ = self.nav_to(me, spot[0], spot[1])
                        else:
                            move = (px * 0.35, py * 0.35)   # hold the spot with a small sidestep
                        tag += "#"
                boost = me["energy"] > P["fight_boost_e"] and target["d"] < P["fight_boost_d"]
                if bold:
                    boost = me["energy"] > 25
            if "midcube" in self.flags:
                lim = 12 if losing else (70 if hp_frac > 0.5 else 40)
                cubes = [c for c in obs.get("cubes", []) if d2(c["pos"], (x, y)) < lim and abs(c["pos"][2] - z) < 3]
                grab = min(cubes, key=lambda c: d2(c["pos"], (x, y)))["pos"] if cubes else None
                if grab is None and not losing and not any(k in ("laser", "lightning") for k in kinds):
                    sp = [p for p in obs.get("pickups", []) if p["kind"] in ("laser", "lightning") and d2(p["pos"], (x, y)) < 60
                          and abs(p["pos"][2] - z) < 3]
                    grab = min(sp, key=lambda p: d2(p["pos"], (x, y)))["pos"] if sp else None
                if grab is not None and d2(grab, (x, y)) > 1.5:
                    move, _ = self.nav_to(me, grab[0], grab[1]); tag += "+"
            if need_zone and (not in_zone or dcur > cur_r - 15):
                zv, _ = self.nav_to(me, self.hide[0], self.hide[1])
                move = (zv[0] * 1.0 + move[0] * 0.4, zv[1] * 1.0 + move[1] * 0.4)
                boost = me["energy"] > 10 if not in_zone else boost
        else:
            # remembered threats without sight: face them
            goal = None
            if need_zone:
                goal = self.hide; mode = "zone"
            elif "repair" in self.flags and (hp_frac < P["rep_lo"] or (t < self.repair_until and hp_frac < P["rep_hi"])):
                goal = (x, y); mode = "repair"
                lt = self.last_threat
                if lt and t - lt[3] < 20 and self.geo.los((lt[0], lt[1], lt[2] + 8), (x, y, z + 6)):
                    spot = self.cover_from(me, [{"pos": [lt[0], lt[1], lt[2]], "d": d2(lt, (x, y)), "id": -7}])
                    if spot:
                        goal = spot
                if self.canopy_g is not None:
                    cs = self.canopy_rest(me, t, cur_c, cur_r)
                    if cs is not None:
                        goal = cs
                goal = (goal[0], goal[1], "repair")
            elif ("heal" in self.flags and self.healing) or ("berserk" in self.flags and self.bz_retreat):
                goal = self.hide; mode = "heal"
            elif getattr(self, "want_special", None) is not None:
                goal = (self.want_special["pos"][0], self.want_special["pos"][1], "weapon"); mode = "weapon"
            else:
                goal = self.loot_goal(me, obs, npcs, kinds, t, cur_c, cur_r)
                if goal is None:
                    goal = self.hide; mode = "hide"
                    if "stayput" in self.flags and self.hide is not None and d2(self.hide, (x, y)) > P["sp_d"]:
                        goal = (x, y); mode = "hold"   # AK: hold ground, the zone logic moves us when it must
                    lt = self.last_threat
                    if "stalk" in self.flags and lt and t - lt[3] < 10 and hp_frac > 0.85 and not need_zone:
                        dx_, dy_ = x - lt[0], y - lt[1]
                        dd_ = max(math.hypot(dx_, dy_), 1e-3)
                        if dd_ > 100:
                            goal = (lt[0] + dx_ / dd_ * 90, lt[1] + dy_ / dd_ * 90); mode = "stalk"   # UK2: stage the attack
                    if "edgepatrol" in self.flags:
                        ang = getattr(self, "patrol_ang", None)
                        if ang is None:
                            ang = math.atan2(y - nc[1], x - nc[0])
                        rr = max(nr - P["ep_in"], 10.0)
                        if d2((nc[0] + math.cos(ang) * rr, nc[1] + math.sin(ang) * rr), (x, y)) < 15:
                            ang += 0.35   # UA: walk around the circle edge looking for enemies
                        self.patrol_ang = ang
                        goal = (nc[0] + math.cos(ang) * rr, nc[1] + math.sin(ang) * rr); mode = "patrol"
                else:
                    mode = goal[2]
            zone_calm = in_zone and dcur < cur_r - 30 and not need_zone
            if self.phase != "normal":
                goal, mode = self.opening(me, t, in_zone, dcur, cur_r, dnext, nr, margin, goal, mode)
            if watch is not None and mode != "zone" and watch["d"] < 140:
                spot = self.cover_from(me, [watch])
                if spot:
                    goal = (spot[0], spot[1], "avoid"); mode = "avoid"
            if goal is not None:
                opening = mode in ("center", "transit") and dcur < cur_r - 40
                weighted = ("cover" in self.flags and mode not in ("zone", "avoid") and zone_calm) or opening
                mv, rem = self.nav_to(me, goal[0], goal[1], weighted=weighted)
                move = mv
                if mode == "avoid" and rem < 2:
                    move = (0.0, 0.0)
                if mode == "repair":
                    k = 4.6 / max(me["stats"].get("walk_speed", 11.7), 1)   # silent
                    move = (move[0] * k, move[1] * k) if rem >= 2 else (0.0, 0.0)
                    boost = False
                if mode in ("hide", "heal") and rem < 4:
                    move = (0.0, 0.0)
                if mode == "zone" and not in_zone:
                    boost = me["energy"] > 10
                # sneak when there's time: walk under the step-sound threshold
                if mode in ("hide",) and rem < 60 and in_zone:
                    k = 4.6 / max(me["stats"].get("walk_speed", 11.7), 1)
                    move = (move[0] * k, move[1] * k)
                # creep: footsteps start at sound.step_speed and carry sound.range.step (40 m); only worth the lost
                # speed when someone was close lately and the zone leaves time for it
                elif ("creep" in self.flags or opening) and mode not in ("zone",) and (zone_calm or opening) and t - self.contact_t < 6:
                    lim = 0.92 * self.cfg.get("sound", {}).get("step_speed", 5.0)
                    k = min(1.0, lim / max(me["stats"].get("walk_speed", 11.7), 1))
                    move = (move[0] * k, move[1] * k)
                    boost = False
                    crept = True
            if "sprint" in self.flags and self.stage == "early" and not mechs and mode in ("weapon", "cube", "crate", "npc") \
                    and me["energy"] > 30:
                boost = True   # J: sprint across open ground while arming up
            if mode in ("npc", "crate") and self.loot_target is not None:
                shoot_target = self.loot_target
            if "econ" in self.flags and shoot_target is None and not mechs:
                shoot_target = self.farm_shot(me, obs, npcs)   # D2: shoot loot while doing anything else
            if "droneback" in self.flags and shoot_target is None and not mechs and getattr(self, "npc_hit", None) \
                    and t - self.npc_hit[1] < 3:
                nb = next((n for n in npcs if n["id"] == self.npc_hit[0] and n["los"]), None)
                if nb is not None:
                    shoot_target = nb; tag += "D"   # UB: shoot back at the drone that hit us

        if self.flags & {"roofesc", "roofloot"}:
            self.plan_climb(me, obs, mechs, hp_frac, t, need_zone)
            if self.climb is not None:
                mv_c, jump_c, boost_c = self.climb_step(me, t)
                if mv_c is not None:
                    act["move"] = [round(mv_c[0], 3), round(mv_c[1], 3)]
                    act["jump"] = jump_c
                    act["boost"] = boost_c
                    self.last_move_mag = 0.0   # no stuck trips while climbing
                    self.climbing = True
        if t < self.unstick_until and self.unstick_dir:
            move = self.unstick_dir
            if self.stuck_ticks == 0 and me["grounded"]:
                act["jump"] = True
            boost = me["energy"] > 20

        if "dodge" in self.flags:
            dv = self.dodge_vec(me, mechs, obs)
            if dv is not None:
                move = dv; boost = me["energy"] > 15; tag += "^"

        # wall-avoid
        mag = math.hypot(*move)
        if mag > 0.05:
            ux, uy = self.safe_dir(x, y, move[0] / mag, move[1] / mag)
            move = (ux * min(mag, 1.0), uy * min(mag, 1.0))
        if "travelboost" in self.flags and mode not in ("fight", "flee") and not mechs and math.hypot(*move) > 0.5 and me["energy"] > P["tb_e"]:
            boost = True
        if getattr(self, "climbing", False):
            self.climbing = False
        else:
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
        if aim is None and not mechs and self.flags & {"treefire", "memfire"} and not need_zone:
            bf = self.blind_fire(me, t, mz, weapons)
            if bf is not None:
                aim, gi, on = bf
                fire[gi] = on; tag += "b"
        if aim is None:
            aim = self.scan_aim(me, obs, move, t)
        act["aim"] = [round(aim[0], 4), round(aim[1], 4)]
        # keep laser charge only while it makes sense
        for i, w in enumerate(weapons):
            if w and w["kind"] == "laser" and w.get("charge", 0) > 0 and not fire[i] and shoot_target is None:
                fire[i] = w["charge"] < w["stats"].get("charge", 0.75) + 1.5  # hold rather than waste
        act["fire"] = fire
        g = weapons[0] if kinds[0] == "gun" else (weapons[1] if kinds[1] == "gun" else None)
        rfrac = P["gun_reload_" + self.stage] if "gunwise" in self.flags else 0.5
        if g and g["reload"] == 0 and not any(fire) and (
                (g["ammo"] < g["stats"]["mag"] * rfrac and mode not in ("fight", "flee"))
                or ("fightreload" in self.flags and mode == "fight" and g["ammo"] < g["stats"]["mag"] * 0.5)):
            act["reload"] = True
        if "scouthop" in self.flags:
            if not mechs and hp_frac > 0.8 and me["energy"] > 80 and not need_zone and mode not in ("repair", "flee") \
                    and t - getattr(self, "hop_t", -99.0) > 15:
                self.hop_t = t
            if t - getattr(self, "hop_t", -99.0) < 1.2:
                act["jump"] = bool(me["grounded"]); act["boost"] = True; tag += "H"   # UK1: look over the rooftops
        if "jumpjuke" in self.flags and me["grounded"] and t - getattr(self, "juke_t", -9.0) > 1.2:
            for m in mechs:
                if m.get("charging") and m.get("aim") and m["off"] < 0.08:
                    act["jump"] = True; self.juke_t = t   # UG1: hop out of the beam line
                    break
        if "dropmortar" in self.flags and "mortar" in kinds and "take" not in act \
                and any("laser" in m.get("weapons", []) and m["d"] < 280 for m in mechs):
            mi = kinds.index("mortar")
            act["drop"] = mi; act["fire"][mi] = False   # UE4: no mortar to cook off
        self.last_mode = mode
        plan = ("F" if self.ffa else "P") + ("b" if self.plan == "build" else "h") if "phased" in self.base_flags else ""
        mf.draw.label(f"{plan}{' ' if plan else ''}{mode}{tag}{'~' if crept else ''} hp{me['hp']:.0f} e{me['energy']:.0f}")
        return act

    # ------------------------------------------------------------------ sub-behaviours
    def update_plan(self, obs, me, kinds, t):
        """B2/B3: decide between 'build' (hide, loot, grow) and 'hunt' (normal aggressive play) for this tick."""
        P = self.P
        total = self.cfg.get("match", {}).get("mechs", 16)
        alive = obs.get("alive", total)
        others_died = (total - alive) - self.my_kills
        # practice house bots never kill each other: deaths we didn't cause, before the zone could have done it, mean
        # other mechs are fighting each other (free-for-all). Later, require two to allow for zone deaths.
        # Two unexplained deaths, not one: on the real server a house bot can also die to an NPC drone or its own mortar,
        # and kill events in skipped observations are lost. Real free-for-alls lose mechs fast, so two comes quickly.
        while len(self.other_deaths) < others_died:
            self.other_deaths.append(t)
        if "timed" in self.base_flags:
            # B9: practice deaths by drones or the zone come slowly; a free-for-all loses mechs fast (sim2 logs: this
            # rule false-alarms in 2% of practice matches and catches 80% of free-for-alls, median 19 s in)
            n = len(self.other_deaths)
            if not self.ffa and n and ((n >= 2 and t < 30) or (n >= 3 and t < 60) or n >= 6):
                self.ffa = True
        elif not self.ffa and others_died >= (2 if t < 200 else 3):
            self.ffa = True
        if "switch" in self.base_flags:
            self.plan = "build" if self.ffa else "hunt"          # B3: A8-style hiding for the rest of a free-for-all
        elif self.ffa:
            self.plan = "hunt" if (alive <= P["ph_ffa_alive"] or self.stage == "late") else "build"
        elif self.plan == "build":
            second = any(k and k != "gun" for k in kinds) and all(kinds)
            if t >= P["ph_prac_t"] or (second and me.get("cubes", 0) >= P["ph_prac_cubes"]):
                self.plan = "hunt"
        extra = set()
        if self.plan == "build":
            extra = {"ambush", "creep"} | (set() if "switch" in self.base_flags else {"buildloot"})
        self.flags = self.base_flags | extra

    def firing_spot(self, me, target, mechs, lo, hi, t):
        """G2: a nearby spot (<= 36 m) that sees the target at our preferred range, is seen by as few other enemies as
        possible and sits near tall buildings (GT Goats-style hold-and-shoot). Cached for 1 s."""
        x, y = me["pos"][0], me["pos"][1]
        c = getattr(self, "_fs_cache", None)
        if c and c[0] == target["id"] and t - c[1] < 1.0 and d2(c[2], (x, y)) < 6:
            return c[3]
        g = self.geo
        tz = target["pos"][2] + 4.5
        others = [m for m in mechs if m is not target and m["d"] < 200][:3]
        mid = 0.5 * (lo + hi)
        best = None
        for r in (0.0, 12.0, 24.0, 36.0):
            for k in range(1 if r == 0 else 12):
                a = k * TAU / 12
                cx, cy = x + math.cos(a) * r, y + math.sin(a) * r
                if r and (not g.is_free(cx, cy) or not g.walk_line(x, y, cx, cy)):
                    continue
                dt_ = math.hypot(target["pos"][0] - cx, target["pos"][1] - cy)
                if dt_ < lo * 0.7 or dt_ > hi * 1.15:
                    continue
                gz = g.ground(cx, cy)
                if not g.los((cx, cy, gz + 7), (target["pos"][0], target["pos"][1], tz)):
                    continue
                seen = sum(1 for m in others if g.los((m["pos"][0], m["pos"][1], m["pos"][2] + 8), (cx, cy, gz + 4.5)))
                j, i = g.cell(cx, cy)
                sc = seen * 40 + abs(dt_ - mid) * 0.3 + r * 0.4 - float(g.cover[j, i]) * 25
                if best is None or sc < best[0]:
                    best = (sc, (cx, cy))
        spot = best[1] if best else None
        self._fs_cache = (target["id"], t, (x, y), spot)
        return spot

    def remember(self, obs, npcs, x, y, t):
        """I2: remember weapon pickups, cubes, drones (and forget what is gone or stale)."""
        seen = set()
        for p in obs.get("pickups", []):
            self.mem[p["id"]] = [p["kind"], p["pos"], t]; seen.add(p["id"])
        for c in obs.get("cubes", []):
            self.mem[c["id"]] = ["cube", c["pos"], t]; seen.add(c["id"])
        for n in npcs:
            self.mem[n["id"]] = ["npc", n["pos"], t]; seen.add(n["id"])
        for mid in list(self.mem):
            kind, pos, ts = self.mem[mid]
            near = d2(pos, (x, y)) < 25 and mid not in seen   # we are there and it is gone
            if near or t - ts > (25 if kind == "npc" else 90):
                del self.mem[mid]

    def plan_climb(self, me, obs, mechs, hp_frac, t, need_zone):
        """H1/H3: start, keep or drop a climb goal."""
        g = self.geo
        x, y, z = me["pos"]
        if self.climb is not None:
            gx, gy, gz, until, why = self.climb
            done = me["grounded"] and abs(z - gz) < 1.0 and d2((x, y), (gx, gy)) < 6
            if t > until or (done and why == "loot") or (why == "escape" and done and hp_frac > 0.6):
                self.climb = None
            return
        if need_zone or not me["grounded"]:
            return
        ground = g.ground(x, y)
        if z > ground + 3:   # already up somewhere
            return
        if "roofesc" in self.flags and hp_frac < self.P["roofesc_hp"] and me["energy"] > 40:
            threats = [m for m in mechs if m["los"] and m["d"] < 120]
            if threats:
                tx = sum(m["pos"][0] for m in threats) / len(threats); ty = sum(m["pos"][1] for m in threats) / len(threats)
                best = None
                for r in (10.0, 16.0, 22.0):
                    for k in range(12):
                        a = k * TAU / 12
                        cx, cy = x + math.cos(a) * r, y + math.sin(a) * r
                        i = int(round(cx - g.x0)); j = int(round(cy - g.y0))
                        if not (0 <= i < g.nx and 0 <= j < g.ny):
                            continue
                        top = float(g.top[j, i]); h = top - float(g.terr[j, i])
                        if not (6 <= h <= 20) or top - z > 22:
                            continue
                        # enough roof around it to stand on
                        if min(float(g.top[min(max(j + dj, 0), g.ny - 1), min(max(i + di, 0), g.nx - 1)])
                               for dj in (-3, 0, 3) for di in (-3, 0, 3)) < top - 1.0:
                            continue
                        away = (cx - x) * (x - tx) + (cy - y) * (y - ty)
                        sc = r - 0.02 * away
                        if best is None or sc < best[0]:
                            best = (sc, (cx, cy, top))
                if best:
                    self.climb = (*best[1], t + 6.0, "escape")
                    return
        if "roofloot" in self.flags and me["energy"] > 50 and not any(m["los"] for m in mechs):
            kinds = [w["kind"] if w else None for w in me["weapons"]]
            items = [(p["pos"], "w") for p in obs.get("pickups", []) if p["kind"] in ("laser", "lightning") and p["kind"] not in kinds]
            items += [(c["pos"], "c") for c in obs.get("cubes", [])]
            for pos, _ in sorted(items, key=lambda it: d2(it[0], (x, y))):
                up = pos[2] - z
                if d2(pos, (x, y)) < 15 and 2.5 < up <= 25:
                    self.climb = (pos[0], pos[1], pos[2], t + 5.0, "loot")
                    return

    def climb_step(self, me, t):
        """Move toward the climb goal; jump at the wall and jet until clear of the edge, then glide on."""
        x, y, z = me["pos"]
        gx, gy, gz, until, why = self.climb
        d = d2((x, y), (gx, gy))
        if d < 0.8:
            return (0.0, 0.0), False, False
        mv = ((gx - x) / d, (gy - y) / d)
        near = d < 14
        jump = bool(near and me["grounded"] and z < gz - 1.0)
        boost = bool(near and not me["grounded"] and z < gz + 2.5 and me["energy"] > 3)
        return mv, jump, boost

    def farm_shot(self, me, obs, npcs):
        """D2: nearest crate (120 m) or drone (110 m) in line of fire, as a target while moving on to something else."""
        mz = self.muzzle(me)
        best = None
        for c in obs.get("crates", []):
            d = math.dist(c["pos"], me["pos"])
            if d < 120 and (best is None or d < best[0]) and self.geo.los(mz, (c["pos"][0], c["pos"][1], c["pos"][2] + 1.2)):
                best = (d, {"pos": c["pos"], "vel": [0, 0, 0], "kind": "crate", "d": d, "hp": c["hp"], "id": c["id"], "los": True})
        for n in npcs:
            if n["d"] < 110 and n["los"] and me["hp"] > 0.6 * me["max_hp"] and (best is None or n["d"] < best[0]):
                best = (n["d"], n)
        return best[1] if best else None

    def dodge_vec(self, me, mechs, obs):
        """C8: a sidestep away from a charging laser lined up on us, or from where a mortar shell will land."""
        x, y, z = me["pos"]
        for m in mechs:   # charging lasers: the beam fires along their aim when released
            if not m.get("charging") or not m["los"] or m["d"] > 285 or m["d"] < 8:
                continue
            dx, dy = math.cos(m["aim"][0]), math.sin(m["aim"][0])
            rx, ry = x - m["pos"][0], y - m["pos"][1]
            if rx * dx + ry * dy <= 0:
                continue
            side = -rx * dy + ry * dx   # signed distance from the beam line
            if abs(side) < 2.5 * (1 + 0.05 * me.get("cubes", 0)) + 2.0:
                sgn = 1.0 if side >= 0 else -1.0
                return (-dy * sgn, dx * sgn)
        if "dodgeb" in self.flags:
            rad = 2.5 * (1 + 0.05 * me.get("cubes", 0))
            for p in obs.get("projectiles", []):
                if p.get("kind") not in ("gun", "npc"):
                    continue
                (px, py, pz), (vx, vy, vz) = p["pos"], p["vel"]
                v2 = vx * vx + vy * vy
                if v2 < 1:
                    continue
                tc = ((x - px) * vx + (y - py) * vy) / v2   # time of closest approach
                if not 0 < tc < 0.45:
                    continue
                cx, cy = px + vx * tc - x, py + vy * tc - y
                if math.hypot(cx, cy) < rad + 1.0 and z - 0.5 < pz + vz * tc < z + 9.5:
                    sp = math.sqrt(v2)
                    nx, ny = -vy / sp, vx / sp   # perpendicular to the bullet, away from its line
                    if nx * (-cx) + ny * (-cy) < 0:
                        nx, ny = -nx, -ny
                    return (nx, ny)
        g = self.wcfg["mortar"].get("gravity", 22)
        splash = self.wcfg["mortar"].get("splash_radius", 7)
        for p in obs.get("projectiles", []):
            if p.get("kind") != "mortar":
                continue
            (px, py, pz), (vx, vy, vz) = p["pos"], p["vel"]
            a, b, c = -0.5 * g, vz, pz - (z + 1.0)
            disc = b * b - 4 * a * c
            if disc < 0:
                continue
            tl = (-b - math.sqrt(disc)) / (2 * a)   # later root: when it comes down to our height
            if tl <= 0 or tl > 6:
                continue
            lx, ly = px + vx * tl, py + vy * tl
            d = math.hypot(x - lx, y - ly)
            if d < splash + 4:
                if d < 0.5:
                    return (-vy / max(math.hypot(vx, vy), 1e-3), vx / max(math.hypot(vx, vy), 1e-3))
                return ((x - lx) / d, (y - ly) / d)
        return None

    def opening(self, me, t, in_zone, dcur, cur_r, dnext, nr, margin, goal, mode):
        """B1: first walk to a half-sheltered spot near the map centre, then to the zone through cover, then normal play."""
        x, y = me["pos"][0], me["pos"][1]
        if self.phase == "center":
            if self.center_spot is None:
                self.center_spot = self.find_center_spot() or (x, y)
            sp = self.center_spot
            # cut it short if the zone edge is getting close or it's taking too long
            if d2((x, y), sp) < 6 or not in_zone or dcur > cur_r - 40 or t > 100:
                self.phase = "transit"
            else:
                return (sp[0], sp[1], "center"), "center"
        if self.phase == "transit":
            if in_zone and dnext < nr - margin * 0.5:
                self.phase = "normal"
            else:
                return (self.hide[0], self.hide[1], "transit"), "transit"
        return goal, mode

    def find_center_spot(self):
        """Walkable cell closest to the map centre that is partly open (exposure 0.35-0.75: not bare, not buried)."""
        import numpy as np
        g = self.geo
        cx, cy = g.x0 + g.nx / 2, g.y0 + g.ny / 2
        ii = np.arange(g.W) * 2 + g.x0 + 1
        jj = np.arange(g.H) * 2 + g.y0 + 1
        d = np.hypot(ii[None, :] - cx, jj[:, None] - cy)
        ok = g.free & (d < 120) & (g.exposure > 0.35) & (g.exposure < 0.75)
        if not ok.any():
            ok = g.free & (d < 120)
        if not ok.any():
            return None
        j, i = divmod(int(np.argmin(np.where(ok, d, 1e9))), g.W)
        return g.cell_xy(j, i)

    loot_target = None

    def loot_goal(self, me, obs, npcs, kinds, t, cur_c, cur_r):
        """Returns (x, y, mode) of something worth walking to, or None."""
        x, y, z = me["pos"]
        hp_frac = me["hp"] / max(1, me["max_hp"])
        self.loot_target = None
        best = None
        P = self.P
        lr = 0.5 if "ambush" in self.flags else 1.0   # A8 hides more: loot only what is close
        lw = 1.0
        if "sprint" in self.flags and self.stage == "early":
            lw = 160.0 / max(P["loot_w_range"], 1)   # J: wide opening weapon search
        if "buildloot" in self.flags:   # B2 build phase: hide, but loot fully (further for weapons) to grow
            lr, lw = 1.0, P["ph_loot_w"]

        def inzone(p, pad=15):
            return d2(p, cur_c) < cur_r - pad

        def reachable(p):
            return abs(p[2] - self.geo.ground(p[0], p[1])) < 2.5 and self.geo.is_free(p[0], p[1]) or d2(p, (x, y)) < 3

        # weapons
        have = [k for k in kinds if k]
        worst = min((self.rank[k] for k in have if k != "gun"), default=-1)
        for p in obs.get("pickups", []):
            if self.blacklist.get(p["id"], 0) > t or p["kind"] in have:
                continue
            r = self.rank.get(p["kind"], -1)
            if kinds[1] is not None and r <= worst:
                continue
            d = d2(p["pos"], (x, y))
            if d < P["loot_w_range"] * lr * lw and inzone(p["pos"]) and reachable(p["pos"]):
                sc = d - r * P["loot_w_rank"]
                if best is None or sc < best[0]:
                    best = (sc, p["pos"][0], p["pos"][1], "weapon")
        # cubes
        for c in obs.get("cubes", []):
            if self.blacklist.get(c["id"], 0) > t:
                continue
            d = d2(c["pos"], (x, y))
            cm = 1.6 if ("endgame" in self.flags and self.stage != "early") else 1.0   # B7: cubes are HP for the end
            if "cubecap" in self.flags and me.get("cubes", 0) >= 10 and d > 40:
                continue
            if d < P["loot_c_range"] * lr * cm and inzone(c["pos"]) and reachable(c["pos"]):
                sc = d - P["loot_c_bonus"]
                if best is None or sc < best[0]:
                    best = (sc, c["pos"][0], c["pos"][1], "cube")
        # supply drops
        for dr in obs.get("drops", []):
            d = d2(dr["pos"], (x, y))
            if "safedrop" in self.flags:
                zn = obs.get("zone", {})
                if not (hp_frac < 0.85 or me.get("cubes", 0) < 10):
                    continue
                if zn.get("next_center") and d2(dr["pos"], zn["next_center"]) > zn.get("next_radius", 1e9) - 8:
                    continue
                if any(d2(m["pos"], dr["pos"]) < 55 for m in obs.get("mechs", []) if m.get("kind") == "mech"):
                    continue   # UB: an enemy is near the drop
            eg = "endgame" in self.flags   # B7: a drop is full HP + energy + 3 cubes: go further, even when hurt
            rp = "repair" in self.flags
            if d < (260 if (eg or rp) else 160) and dr["lands_in"] < d / 11 + 3 and hp_frac > (0.15 if (eg or rp) else 0.3):
                if dr.get("holder") not in (None, me["id"]) and dr.get("progress", 0) > 2:
                    continue
                sc = d - (140 if (eg or ("repair" in self.flags and hp_frac < 0.8)) else 40)
                if best is None or sc < best[0]:
                    best = (sc, dr["pos"][0], dr["pos"][1], "drop")
        if "memory" in self.flags:
            for mid, (kind, pos, ts) in self.mem.items():
                if self.blacklist.get(mid, 0) > t or not inzone(pos):
                    continue
                d = d2(pos, (x, y))
                if kind == "cube" and "cubecap" in self.flags and me.get("cubes", 0) >= 10 and d > 40:
                    continue
                if kind == "cube" and d < 150 and reachable(pos):
                    sc = d - 30
                elif kind in ("laser", "lightning", "mortar") and kind not in have and d < 160 and reachable(pos) \
                        and (kinds[1] is None or self.rank.get(kind, -1) > worst):
                    sc = d - self.rank.get(kind, 0) * 20 - (50 if kinds[1] is None else 0)
                else:
                    continue
                if best is None or sc < best[0]:
                    best = (sc, pos[0], pos[1], "weapon" if kind != "cube" else "cube")
        if best is not None:
            return best[1], best[2], best[3]
        if "memory" in self.flags and hp_frac > 0.5 and "nodrone" not in self.flags:
            # farm remembered drones: walk into gun range (~38 m) of the nearest one
            dr = [(d2(pos, (x, y)), pos) for mid, (kind, pos, ts) in self.mem.items() if kind == "npc" and d2(pos, (x, y)) < 170]
            if dr and not any(n["los"] and n["d"] < 90 for n in npcs):
                dd, pos = min(dr, key=lambda q: q[0])
                if dd > 38:
                    return pos[0], pos[1], "npc"
        # NPC drones: farm cubes when healthy
        if hp_frac > (0.85 if "nodrone" in self.flags else 0.7):
            cands = [n for n in npcs if n["d"] < (65 if "nodrone" in self.flags else 140 if "econ" in self.flags else 90) and n["los"] and inzone(n["pos"], 5)]
            if cands:
                n = min(cands, key=lambda n: n["d"] + n["hp"] * 0.1)
                self.loot_target = n
                gx, gy = n["pos"][0], n["pos"][1]
                if n["d"] > 35:
                    return gx, gy, "npc"
                return x, y, "npc"
        # crates
        crates_ok = hp_frac > P["crate_hp"]
        if "ambush" in self.flags:
            crates_ok = hp_frac > 0.9
        if "buildloot" in self.flags:   # shoot crates only while no mech can see or hear-locate us nearby
            crates_ok = hp_frac > P["crate_hp"] and not any(m["kind"] == "mech" for m in obs.get("mechs", []))
        if "gunwise" in self.flags and self.stage == "late":
            crates_ok = False   # late game: no gunfire just for loot
        if crates_ok:
            mz = self.muzzle(me)
            cands = []
            for c in obs.get("crates", []):
                d = d2(c["pos"], (x, y))
                if d < P["crate_range"] * lr and inzone(c["pos"], 5) and self.geo.los(mz, (c["pos"][0], c["pos"][1], c["pos"][2] + 1.2)):
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
            cand = [(self.rank.get(kinds[i], -1), i) for i in (0, 1) if kinds[i] and kinds[i] != "gun"]
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
            if worst_slot is not None and self.rank.get(p["kind"], -1) > self.rank.get(kinds[worst_slot], -1):
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
        if "ambush" not in self.flags:   # only while hiding (A8, B2/B3 build); in hunt mode play exactly like A9
            return self._cover_search(me, enemies)
        enemies = sorted(enemies, key=lambda m: m.get("d", 0))[:3]
        ck = tuple(sorted(m.get("id", -1) for m in enemies))
        cc = self._cover_cache
        if cc and cc[1] == ck and self.t_now - cc[0] < 0.4 and d2((x, y), cc[2]) < 3:
            return cc[3]
        res = self._cover_search(me, enemies)
        self._cover_cache = (self.t_now, ck, (x, y), res)
        return res

    def _cover_search(self, me, enemies):
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
        if "hearlaser" in self.flags and t < self.laser_snd_until and self.laser_snd is not None:
            return [self.laser_snd, 0.0]
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
        order = sorted([(i, w) for i, w in enumerate(weapons) if w],
                       key=lambda iw: -(self.weapon_value(iw[1]["kind"], dist, los, iw[1]) + self.tri_bonus(iw[1]["kind"], tg)))
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
        if "gunlaser" in self.flags and primary is not None and weapons[primary]["kind"] == "laser":
            lw = weapons[primary]
            if lw.get("charge", 0) < lw["stats"].get("charge", 0.75) - 0.18:
                for g in weapons:
                    if g and g["kind"] == "gun" and los and dist < g["stats"]["range"] * 0.95 and g["ammo"] > 0 and g["reload"] == 0:
                        aim = self.aim_at(mz, self.lead(mz, tp, vel, g["stats"]["speed"]))
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
                if los and dist < st["range"] * 0.95 and w["cooldown"] <= 0.1 and w["ammo"] > 0 and w["reload"] == 0 \
                        and self.gun_worth_it(tg, dist, rad, vel, st, mz):
                    lp = self.lead(mz, tp, vel, st["speed"])
                    ga = self.aim_at(mz, lp)
                    gt = tol * self.P["gun_tol"] + 0.012
                    if abs(wrap(cur[0] - ga[0])) < gt and abs(cur[1] - ga[1]) < gt:
                        fire[i] = True
            elif k == "laser":
                full = st.get("charge", 0.75)
                chg = w.get("charge", 0)
                if los and dist < st["range"]:
                    if w["cooldown"] <= 0.05:
                        lethal = False
                        if "lethal" in self.flags and tg.get("kind") == "mech" and tg.get("hp") is not None and chg >= st.get("min_charge", 0.15):
                            mf_ = st.get("min_frac", 0.35)
                            dmg = st.get("damage", 315) * (mf_ + (1 - mf_) * min(chg / full, 1.0))
                            lethal = 0.8 * dmg >= tg["hp"] and yaw_err < tol and pitch_err < tol + 0.01
                        if lethal:
                            fire[i] = False  # AG1: this much charge already kills: release now
                        elif chg < full * 0.97:
                            fire[i] = True  # keep charging
                        else:
                            # fully charged: release when on target
                            on = yaw_err < tol and pitch_err < tol + 0.01
                            fire[i] = not on
                elif chg > 0:
                    fire[i] = chg < full + 1.6  # wait for line of sight
                elif "precharge" in self.flags and (dist < 60 or (self.lp_on and dist < 200)) and w["cooldown"] <= 0.05:
                    fire[i] = True   # K2: be charged when it steps back into view
            elif k == "lightning":
                cone = math.radians(st.get("cone_deg", 30)) * 0.45
                if "lightrest" in self.flags:
                    self.light_rest = me["energy"] < 65 if getattr(self, "light_rest", False) else me["energy"] < 20
                if los and dist < st["range"] * 0.95 and w["cooldown"] <= 0.1 and me["energy"] > st.get("energy", 3) + 6 \
                        and not ("lightrest" in self.flags and self.light_rest):
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

    def corner_spot(self, me, tg, mechs, lo, hi, t):
        """UJ1: a nearby spot with a shot at the target and a hidden step within 6 m (re-chosen every 0.5 s)."""
        if t - getattr(self, "corner_t", -9.0) < 0.5:
            return getattr(self, "corner_xy", None)
        self.corner_t = t
        g = self.geo
        x, y, z = me["pos"]
        mh = me["stats"].get("muzzle_height", 7.0)
        tc = (tg["pos"][0], tg["pos"][1], tg["pos"][2] + 4.5)
        foes = sorted([m for m in mechs if m["d"] < 200], key=lambda m: m["d"])[:3] or [tg]
        def hidden(px, py):
            pz = g.ground(px, py) + 6.0
            return all(not g.los((m["pos"][0], m["pos"][1], m["pos"][2] + 8.0), (px, py, pz)) for m in foes)
        def escape(px, py):
            for k in range(8):
                a = k * math.pi / 4
                qx, qy = px + math.cos(a) * 6, py + math.sin(a) * 6
                if g.is_free(qx, qy) and hidden(qx, qy):
                    return True
            return False
        mid = (lo + hi) / 2
        best, bxy = None, None
        for k in range(-1, 12):
            if k < 0:
                px, py, r = x, y, 0.0
            else:
                a = k * math.pi / 6
                px, py, r = x + math.cos(a) * 6, y + math.sin(a) * 6, 6.0
                if not g.is_free(px, py) or not g.walk_line(x, y, px, py):
                    continue
            if not g.los((px, py, g.ground(px, py) + mh), tc):
                continue
            d = math.hypot(tc[0] - px, tc[1] - py)
            sc = (40 if escape(px, py) else 0) - 0.5 * abs(d - mid) - 1.0 * r
            if best is None or sc > best:
                best, bxy = sc, (px, py)
        self.corner_xy = bxy
        return bxy

    def canopy_rest(self, me, t, cur_c, cur_r):
        """AH1: the densest tree cover within 40 m (crowns hide mechs from anyone beyond 45 m)."""
        x, y = me["pos"][0], me["pos"][1]
        if self.canopy_spot is not None and t - self.canopy_t < 10:
            return self.canopy_spot
        g = self.geo; best = None
        for dx in range(-40, 41, 4):
            for dy in range(-40, 41, 4):
                if dx * dx + dy * dy > 1600:
                    continue
                px, py = x + dx, y + dy
                j, i = g.cell(px, py)
                c = float(self.canopy_g[j, i])
                if c < 0.6 or not g.is_free(px, py) or d2((px, py), cur_c) > cur_r - 25:
                    continue
                sc = c * 50 - math.hypot(dx, dy)
                if best is None or sc > best[0]:
                    best = (sc, px, py)
        self.canopy_t = t
        self.canopy_spot = (best[1], best[2]) if best else None
        return self.canopy_spot

    def blind_fire(self, me, t, mz, weapons):
        """AG2/AG3: gun shots at mechs we can't see but know are there (trees hide mechs, they don't stop shots)."""
        if "lateonly" in self.flags and self.stage != "late":
            return None   # UI: only in the last circles
        gi = next((i for i, w in enumerate(weapons) if w and w["kind"] == "gun"), None)
        if gi is None:
            return None
        g = weapons[gi]
        if g["reload"] > 0 or g["ammo"] < g["stats"]["mag"] * 0.4:
            return None
        cur = me["aim"]
        rng = g["stats"]["range"] * 0.9
        if "memfire" in self.flags and self.seen_mech:
            last = max(self.seen_mech.items(), key=lambda kv: kv[1][0])
            ts, pos, vel = last[1]
            dt = t - ts
            if 0 < dt < 2.0:
                k = min(dt, 1.0)
                tp = (pos[0] + vel[0] * k, pos[1] + vel[1] * k, pos[2] + 4.5)
                if math.dist(tp, mz) < rng and self.geo.los(mz, tp):
                    aim = self.aim_at(mz, self.lead(mz, tp, vel, g["stats"]["speed"]))
                    on = g["cooldown"] <= 0.1 and abs(wrap(cur[0] - aim[0])) < 0.03 and abs(cur[1] - aim[1]) < 0.03
                    return aim, gi, on
        if "treefire" in self.flags and self.mech_snd is not None and t - self.mech_snd[1] < 0.6:
            b = self.mech_snd[0]
            if abs(wrap(cur[0] - b)) < math.radians(self.vis.get("fov_deg", 120)) / 2:
                end = (mz[0] + math.cos(b) * 60, mz[1] + math.sin(b) * 60, mz[2])
                if self.geo.los(mz, end):
                    aim = [b, 0.0]
                    on = g["cooldown"] <= 0.1 and abs(wrap(cur[0] - b)) < 0.03
                    return aim, gi, on
            else:
                return [b, 0.0], gi, False   # turn toward it first: if it is in the open we will see it
        return None

    def tri_bonus(self, kind, tg):
        """UE3: the rules' weapon triangle: laser beats mortar, mortar beats lightning, lightning beats laser."""
        if "triangle" not in self.flags or tg.get("kind") != "mech":
            return 0.0
        theirs = tg.get("weapons", [])
        beats = {"laser": "mortar", "mortar": "lightning", "lightning": "laser"}
        return 2.0 if beats.get(kind) in theirs else 0.0

    def gun_worth_it(self, tg, dist, rad, vel, st, mz):
        """A9: open fire with the gun only inside this stage's range (unless it is shooting us) and when a shot has a
        fair chance: spread (spread_deg) plus lead error from the target's sideways speed, against its radius."""
        if "gunwise" not in self.flags:
            return True
        P = self.P
        hit_us = tg.get("kind") == "mech" and tg.get("id") in self.attackers and self.attackers[tg["id"]] > self.t_now - 4
        if dist > P["gun_maxd_" + self.stage] and not hit_us:
            return False
        dx, dy = tg["pos"][0] - mz[0], tg["pos"][1] - mz[1]
        L = max(math.hypot(dx, dy), 1e-3)
        v_side = abs(vel[0] * (-dy / L) + vel[1] * (dx / L))
        tof = dist / max(st.get("speed", 252), 1)
        sig = math.hypot(dist * math.tan(math.radians(st.get("spread_deg", 0.6))), 0.35 * v_side * tof + 0.3)
        p_hit = 1 - math.exp(-(rad * rad) / (2 * sig * sig))
        return p_hit >= P["gun_phit_" + self.stage] or hit_us

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
