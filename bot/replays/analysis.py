# -*- coding: utf-8 -*-
"""
Turn a parsed recorded game into per-player performance data.

Everything here is derived from player commands and the game's periodic sync
snapshots, so numbers are proxies: age times are when the age-up was clicked,
unit counts are queue clicks (cancelled units included) and each unit line is
named after its base unit (Archer covers Crossbowmen and Arbalesters, etc.).
"""
import json
import math
import re
from functools import lru_cache
import os
from collections import Counter, defaultdict

from .rec_parser import reference

# Resource weights for army value: gold is the scarcest, stone next, then food and wood.
RESOURCE_WEIGHTS = dict(gold=1.5, stone=1.2, food=1.0, wood=1.0)
ARMY_COMMANDS = {'FORMATION', 'STANCE', 'PATROL', 'DE_ATTACK_MOVE', 'ATTACK_GROUND', 'GUARD', 'FOLLOW'}
ARMY_ALIVE_GRACE = 90000  # a unit counts as on the map until 90 s after its owner last commanded it
POP_CAP = 200


@lru_cache(maxsize=1)
def _costs():
	with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'costs.json'), encoding='utf-8') as f:
		return json.load(f)['costs']


def tech_cost(tech_id):
	return sum(_costs_all().get('techs', {}).get(str(tech_id), {}).values())


@lru_cache(maxsize=1)
def _costs_all():
	with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'costs.json'), encoding='utf-8') as f:
		return json.load(f)


def unit_value(object_id):
	""" Resource-weighted cost of a unit or building (gold counts most). """
	return sum(RESOURCE_WEIGHTS.get(k, 1.0) * v for k, v in _costs().get(str(object_id), {}).items())

AGES = ('Feudal Age', 'Castle Age', 'Imperial Age')
MILITARY_SKIP = {'Villager', 'Trade Cart', 'Trade Cog', 'Fishing Ship', 'Transport Ship', 'Monk', 'Missionary'}
LINE_NAMES = {
	'Archer': 'Archers', 'Skirmisher': 'Skirmishers', 'Cavalry Archer': 'Cavalry Archers', 'Hand Cannoneer': 'Hand Cannoneers',
	'Scout Cavalry': 'Scouts/Hussars', 'Knight': 'Knights', 'Camel Rider': 'Camels', 'Battle Elephant': 'Elephants',
	'Steppe Lancer': 'Steppe Lancers', 'Militia': 'Militia line', 'Spearman': 'Spearmen/Halbs', 'Eagle Scout': 'Eagles',
	'Mangonel': 'Mangonels', 'Battering Ram': 'Rams', 'Scorpion': 'Scorpions', 'Bombard Cannon': 'Bombard Cannons',
	'Trebuchet': 'Trebuchets', 'Monk': 'Monks', 'Galley': 'Galleys', 'Fire Galley': 'Fire ships',
	'Demolition Raft': 'Demo ships', 'Cannon Galleon': 'Cannon Galleons',
	'War Galley': 'Galleys', 'Galleon': 'Galleys', 'Fire Ship': 'Fire ships', 'Demolition Ship': 'Demo ships',
	'Hulk': 'Hulks', 'War Hulk': 'Hulks', 'Carrack': 'Carracks', 'Longboat': 'Longboats', 'Caravel': 'Caravels',
	'Dromon': 'Dromons', 'Turtle Ship': 'Turtle Ships', 'Thirisadai': 'Thirisadai',
	'Camel Scout': 'Camels',
}
WARSHIPS = {'Galley', 'War Galley', 'Galleon', 'Fire Galley', 'Fire Ship', 'Demolition Raft', 'Demolition Ship',
	'Cannon Galleon', 'Hulk', 'War Hulk', 'Carrack', 'Longboat', 'Caravel', 'Dromon', 'Turtle Ship', 'Thirisadai'}
CASTLES = {'Castle', 'Krepost', 'Donjon', 'Fortress'}
ECO_BUILDINGS = {'Town Center', 'House', 'Farm', 'Mill', 'Lumber Camp', 'Mining Camp', 'Market', 'Dock',
	'Folwark', 'Pasture', 'Feitoria', 'Settlement', 'Mule Cart'}
TOWERS = {'Watch Tower', 'Guard Tower', 'Keep', 'Bombard Tower'}
ARMY_ORDERS = {'MOVE', 'ORDER', 'DE_ATTACK_MOVE', 'PATROL', 'ATTACK_GROUND'}


def mmss(ms):
	if ms is None:
		return "—"
	s = int(ms // 1000)
	return f"{s // 60}:{s % 60:02d}"


def _name(table, idx):
	return reference()[table].get(str(idx), str(idx))


def civ_name(civ_id):
	return reference()['civilizations'].get(str(civ_id), {}).get('name', f"Civ {civ_id}")


def _minmax(values, invert=False):
	""" Scale a {pid: value} dict to 1..10 within the lobby. None counts as worst. """
	known = [v for v in values.values() if v is not None]
	if not known:
		return {k: 5.0 for k in values}
	lo, hi = min(known), max(known)
	out = {}
	for k, v in values.items():
		if v is None:
			out[k] = 1.0
		elif hi == lo:
			out[k] = 5.5
		else:
			x = (v - lo) / (hi - lo)
			out[k] = 1 + 9 * ((1 - x) if invert else x)
	return out


def analyze(rec: dict, meta: dict = None) -> dict:
	"""
	rec: output of rec_parser.parse().
	meta: optional {profile_id: dict(rating=int, won=bool, team=int, alias=str)} from World's Edge.
	"""
	meta = meta or {}
	ref = reference()
	A = rec['actions']
	end = rec['duration']
	# Who resigned (for the result) comes from the whole game, but everything else is judged only up to
	# the first resignation: after that the game is decided and players leave, delete or idle.
	all_resigns = {}
	for a in A:
		if a['a'] == 'RESIGN' and a.get('player_id') is not None and a['player_id'] not in all_resigns:
			all_resigns[a['player_id']] = a['t']
	if all_resigns:
		end = min(all_resigns.values())
		A = [a for a in A if a['t'] <= end]
		rec = dict(rec, syncs=[s for s in rec['syncs'] if s['t'] <= end])
	players = {p['number']: dict(p) for p in rec['players']}
	for p in players.values():
		p['civ'] = civ_name(p['civ_id'])
		m = meta.get(p['profile_id'], {})
		p['rating'] = m.get('rating')  # World's Edge team rating
		p['elo'] = m.get('elo')  # BombayBot Elo used to make the match
		p['user_id'] = m.get('user_id')  # Discord member matched by name, if any
		p['won'] = m.get('won')
		if m.get('alias'):
			p['name'] = m['alias']

	# ---- starting positions from the first buildings
	pos = {}
	for pid in players:
		pts = [(a['x'], a['y']) for a in A if a['a'] == 'BUILD' and a.get('player_id') == pid and a['t'] < 240000]
		if pts:
			pos[pid] = (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
	if not pos:
		raise ValueError("No buildings found in the recorded game.")
	cx = sum(x for x, _ in pos.values()) / len(pos)
	cy = sum(y for _, y in pos.values()) / len(pos)

	def base_of(x, y):
		return min(pos, key=lambda p: (pos[p][0] - x) ** 2 + (pos[p][1] - y) ** 2)

	# ---- seats: ends of each team's arc are flanks
	ring = sorted(pos, key=lambda p: math.atan2(pos[p][1] - cy, pos[p][0] - cx))
	teams = defaultdict(list)
	for pid, p in players.items():
		teams[p['team_id']].append(pid)
	for pid, p in players.items():
		p['seat'] = None
		if pid in ring and len(teams[p['team_id']]) >= 3:
			i = ring.index(pid)
			neigh = (ring[i - 1], ring[(i + 1) % len(ring)])
			p['seat'] = 'Flank' if any(players[n]['team_id'] != p['team_id'] for n in neigh) else 'Pocket'

	# ---- winner from resigns when World's Edge did not tell us
	resigns = {p: t for p, t in all_resigns.items() if p in players}
	if all(p['won'] is None for p in players.values()) and resigns:
		for tid, members in teams.items():
			lost = all(m in resigns for m in members)
			for m in members:
				players[m]['won'] = not lost if len(teams) == 2 else None
		if len(teams) == 2 and all(players[m]['won'] for m in sum(teams.values(), [])):
			for m in players:
				players[m]['won'] = None

	# ---- object ownership and references
	villagers = set()
	for a in A:
		if a['a'] in ('BUILD', 'WALL', 'REPAIR'):
			villagers.update(a.get('object_ids') or [])
	refs = defaultdict(list)
	for a in A:
		for o in a.get('object_ids') or []:
			refs[o].append(a)
		if a.get('target_id', 0) and a['target_id'] > 0:
			refs[a['target_id']].append(a)

	# ---- per player basics
	stats = {}
	for pid, p in players.items():
		mine = [a for a in A if a.get('player_id') == pid]
		ages = {}
		for a in mine:
			if a['a'] == 'RESEARCH':
				n = _name('technologies', a['technology_id'])
				if n in AGES:  # clicked again (Town Center destroyed or research cancelled): the last click counted
					ages[n] = a['t']
		units = Counter()
		first_military = None
		vills = 0
		vill_times = []
		for a in mine:
			if a['a'] == 'DE_QUEUE':
				n = _name('objects', a['unit_id'])
				amt = a.get('amount', 1) or 1
				if n == 'Villager':
					vills += amt
					vill_times += [a['t']] * amt
				else:
					units[n] += amt
					if n not in MILITARY_SKIP and first_military is None:
						first_military = a['t']
		builds = [a for a in mine if a['a'] == 'BUILD']
		bcount = Counter(_name('objects', a['building_id']) for a in builds)
		ca = ages.get('Castle Age')
		tc_after_castle = sum(1 for a in builds if ca and ca <= a['t'] <= ca + 7 * 60000
			and _name('objects', a['building_id']) == 'Town Center')
		tc_moves = sum(1 for a in builds if a['t'] > 20 * 60000 and _name('objects', a['building_id']) == 'Town Center'
			and base_of(a['x'], a['y']) != pid)
		minutes = max(end / 60000, 1)
		stats[pid] = dict(
			ages=ages, units=units, villagers_queued=vills, _vill_times=vill_times, first_military=first_military, builds=bcount,
			apm=round(len(mine) / minutes),
			tc_built=bcount.get('Town Center', 0), tc_after_castle=tc_after_castle, tc_moves=tc_moves,
			towers=sum(bcount.get(t, 0) for t in TOWERS),
			flares=sum(1 for a in mine if a['a'] == 'FLARE'),
			buys=sum(1 for a in mine if a['a'] == 'BUY'), sells=sum(1 for a in mine if a['a'] == 'SELL'),
			resign=resigns.get(pid),
		)

	# ---- tributes
	trib_out, trib_in = Counter(), Counter()
	trib_pairs = Counter()
	for a in A:
		if a['a'] == 'DE_TRIBUTE':
			amt = sum(a.get(k, 0) or 0 for k in ('food', 'wood', 'gold', 'stone'))
			to = a.get('target_player_id')
			if amt > 0 and to in players:
				trib_out[a['player_id']] += amt
				trib_in[to] += amt
				trib_pairs[(a['player_id'], to)] += amt

	# ---- which army orders were offensive: aimed into an enemy base, at an enemy unit or building,
	# or near enemy units (within ENGAGE_RADIUS tiles of where an enemy army was ordered within a minute).
	offensive = find_engagements(A, players, base_of, villagers)

	# ---- army orders: pressure dealt, pressure received, help given to allies
	press_out, press_in, help_out = Counter(), Counter(), Counter()
	press_matrix = Counter()
	timeline_in = defaultdict(Counter)  # defender -> minute -> orders
	into_by = defaultdict(Counter)  # (attacker, defender) -> minute -> army orders into the defender's base
	team_into = defaultdict(Counter)  # (attacking team, defender) -> minute -> orders
	attack_times = defaultdict(list)  # attacker -> times of army orders into enemy bases
	for a in A:
		if a['a'] not in ARMY_ORDERS or a.get('x') is None:
			continue
		pid = a.get('player_id')
		ids = a.get('object_ids') or []
		if pid not in players or not ids or all(i in villagers for i in ids):
			continue
		if a['x'] <= 0 or a['y'] <= 0:
			continue
		b = base_of(a['x'], a['y'])
		in_enemy_base = players[b]['team_id'] != players[pid]['team_id']
		if id(a) in offensive:
			press_out[pid] += 1
			attack_times[pid].append(a['t'])
		if in_enemy_base:  # pressure received is counted per base
			press_in[b] += 1
			press_matrix[(pid, b)] += 1
			timeline_in[b][int(a['t'] // 60000)] += 1
			into_by[(pid, b)][int(a['t'] // 60000)] += 1
			team_into[(players[pid]['team_id'], b)][int(a['t'] // 60000)] += 1
		elif b != pid:
			help_out[pid] += 1

	# ---- castles: placement, where, outcome
	castles = []
	deleted = {a['object_ids'][0]: a['t'] for a in A if a['a'] == 'DELETE' and a.get('object_ids')}
	for b in A:
		if b['a'] != 'BUILD' or _name('objects', b['building_id']) not in CASTLES:
			continue
		pid, x, y = b['player_id'], b['x'], b['y']
		if pid not in players:
			continue
		where = base_of(x, y)
		cand = Counter()
		for a in A:
			if a['a'] == 'ORDER' and a['t'] >= b['t'] and a.get('target_id', 0) > 0 and a.get('x') is not None \
					and math.dist((a['x'], a['y']), (x, y)) < 2.6:
				cand[a['target_id']] += 1
		status, oid, lost_at = 'unclear', None, None
		produced = []
		for o, _ in cand.most_common(2):
			r = refs.get(o, [])
			prod = [a for a in r if a['a'] in ('DE_QUEUE', 'RESEARCH', 'GATHER_POINT') and a.get('player_id', pid) == pid
				and o in (a.get('object_ids') or [])]
			if o in deleted:
				status, oid = 'deleted', o
				break
			if prod:
				oid = o
				produced = prod
				status = 'up'
				break
		if status == 'up':
			last = max(a['t'] for a in refs[oid])
			enemy_hits = [a for a in refs[oid] if a.get('target_id') == oid and a.get('player_id') in players
				and players[a['player_id']]['team_id'] != players[pid]['team_id']]
			if enemy_hits and last == max(a['t'] for a in enemy_hits) and end - last > 90000:
				status = 'lost'
				lost_at = last
		trained = Counter(_name('objects', a['unit_id']) for a in produced if a['a'] == 'DE_QUEUE')
		castles.append(dict(
			t=b['t'], player=pid, kind=_name('objects', b['building_id']), base=where,
			forward=where != pid and players[where]['team_id'] != players[pid]['team_id'],
			status=status, deleted_at=deleted.get(oid), trained=trained.most_common(2), oid=oid,
			lost_at=lost_at, x=x, y=y, building_id=b['building_id'], act=id(b)
		))

	# Objects that queue castle units (Trebuchets, Petards, or the unique units a known castle trained) are castles.
	castle_units = defaultdict(lambda: {'Trebuchet', 'Petard'})
	for c in castles:
		if c['status'] in ('up', 'lost') and c.get('oid'):
			castle_units[c['player']].update(n for n, _ in c['trained'])
	castle_ids = defaultdict(dict)  # player -> {object_id: first use}
	for a in A:
		if a['a'] == 'DE_QUEUE' and _name('objects', a['unit_id']) in castle_units[a.get('player_id')]:
			for o in a.get('object_ids') or []:
				castle_ids[a.get('player_id')].setdefault(o, a['t'])
	for pid in players:
		mine = sorted((c for c in castles if c['player'] == pid), key=lambda c: c['t'])
		used = {c.get('oid') for c in mine}
		free = sorted(((t, o) for o, t in castle_ids[pid].items() if o not in used))
		for c in mine:
			if c['status'] != 'unclear':
				continue
			match = next(((t, o) for t, o in free if t >= c['t']), None)
			if match:
				free.remove(match)
				c['status'] = 'up'
				c['oid'] = match[1]
				c['trained'] = Counter(_name('objects', a['unit_id']) for a in A if a['a'] == 'DE_QUEUE'
					and match[1] in (a.get('object_ids') or [])).most_common(2)

	# ---- empire size from sync snapshots
	curve = defaultdict(list)
	for s in rec['syncs']:
		for pid, d in s['players'].items():
			if pid in players:
				curve[pid].append((s['t'], d['obj_count'], d['total_res']))

	def obj_at(pid, frac):
		c = curve.get(pid)
		if not c:
			return None
		t = end * frac
		return min(c, key=lambda r: abs(r[0] - t))[1]

	peak_bank = {}
	for pid in players:
		late = [r[2] for r in curve.get(pid, []) if r[0] > 20 * 60000]
		peak_bank[pid] = max(late) if late else None

	# ---- ratings (scaled within this lobby)
	pids = list(players)
	castle_click = {p: stats[p]['ages'].get('Castle Age') for p in pids}
	imp_click = {p: stats[p]['ages'].get('Imperial Age') for p in pids}
	attacked = {p: {m for m, n in timeline_in[p].items() if n >= ATTACK_MINUTE_ORDERS} |
		{int(c['t'] // 60000) for c in castles if c['forward'] and c['base'] == p} for p in pids}
	for p in pids:
		stats[p]['_attacked'] = attacked[p]
	first_mil = {p: stats[p]['first_military'] for p in pids}
	exec_raw = [castle_click, imp_click, None, first_mil, None]  # None: always scored
	exec_parts = [
		_minmax(castle_click, invert=True),
		_minmax(imp_click, invert=True),
		None,  # villager value: filled in once villagers alive are estimated (below)
		_minmax(first_mil, invert=True),
	]  # unplanned floating is added once it is known (below)
	# Pressure: the army a player keeps on the map (units plus castles and towers, valued by cost),
	# the army value sent into enemy bases, and forward castles and towers.
	imp_at = {p: stats[p]['ages'].get('Imperial Age') for p in pids}
	water = water_weight(rec.get('map_name'))
	structs = structure_list(A, players, castles, base_of, end)
	army = army_presence(A, players, base_of, villagers, end, offensive, imp_at, water, structs)
	sent = _minmax({p: army[p]['value_sent'] for p in pids})
	# Forward castles, Donjons and towers count by cost and by what they did: stood, held the area, gained ground.
	forward_raw = forward_value(A, players, structs, villagers, end)
	for p in pids:
		mine = [x for x in structs if x['player'] == p and x['forward']]
		stats[p]['forward_value'] = round(forward_raw[p], 2)
		stats[p]['forward_effect'] = round(sum(x['effect'] * x['weight'] for x in mine) / sum(x['weight'] for x in mine), 2) if mine else None
		stats[p]['forward_structures'] = [dict(kind=x['kind'], t=x['t0'], base=x['base'], built=x['known'], effect=x['effect'],
			held=x['hold'], presence=x['presence'], gain=x['gain'], push=x['push'], pushes=x['pushes'], trade=x['trade']) for x in mine]
	for p in pids:
		stats[p]['army'] = army[p]
	vest = villager_estimate(A, players, villagers, {p: army[p]['_alive'] for p in pids}, end)
	vill_killed = Counter()
	for p in pids:
		for e, n in vest[p]['killers'].items():
			vill_killed[e] += n
	for p in pids:
		stats[p]['villagers_killed'] = round(vill_killed[p])
		stats[p]['_vill_kill_value'] = vill_killed[p] * VILLAGER_KILL_VALUE
	# Castle Age timing: a late click after a Feudal army is fine when production kept running, so those
	# players score whichever is better, the click time or their Feudal production uptime.
	for p in pids:
		feudal, castle = stats[p]['ages'].get('Feudal Age'), stats[p]['ages'].get('Castle Age')
		mil_times, feudal_units = [], 0
		for a in A:
			if a['a'] == 'DE_QUEUE' and a.get('player_id') == p:
				n = _name('objects', a['unit_id'])
				if n not in NOT_ARMY and n not in WORKER_TRAIN:
					mil_times.append(a['t'])
					if feudal and feudal <= a['t'] < (castle or end):
						feudal_units += a.get('amount', 1) or 1
		uptime = production_uptime(vest[p]['busy'], vest[p]['first_use'], mil_times, feudal, castle or end,
			saving_minutes(A, p, stats[p]['ages']))
		stats[p]['feudal_units'] = feudal_units
		stats[p]['feudal_uptime'] = uptime
		if feudal_units >= FEUDAL_ARMY_MIN and uptime is not None:
			exec_parts[0][p] = max(exec_parts[0][p], 1 + 9 * uptime)
	for p in pids:
		v = vest[p]
		stats[p]['_vill_made'], stats[p]['_vill_alive'] = v['made'], v['alive']
		stats[p]['villagers_made'] = v['villagers']
		stats[p]['fishing_ships_made'] = v['fishing']
		stats[p]['villagers_lost'] = len(v['lost'])  # villagers and fishing ships killed or deleted
		stats[p]['fishing_ships_lost'] = v.get('ships_lost', 0)
		stats[p]['villagers_peak'] = max(v['alive'], default=0)
		stats[p]['villagers_at'] = {m: v['alive'][m] for m in (20, 30, 40) if m < len(v['alive'])}
	banked = {p: unplanned_floating(A, p, curve.get(p, []), army[p]['_alive'], stats[p], end) for p in pids}
	# Floating is judged against what the player was spending: 700 banked is fine for someone turning 3000 a
	# minute into units, bad for someone spending 1000. So the score uses minutes of spending sitting idle.
	floating = {}
	for p in pids:
		rate = spending_rate(A, p, FLOAT_FROM, end)
		stats[p]['floating'] = banked[p]
		stats[p]['spend_per_min'] = round(rate)
		floating[p] = round(banked[p] / max(rate, 100), 2)
		stats[p]['float_minutes'] = floating[p]
	exec_parts[2] = _minmax({p: villager_value(stats[p]['_vill_made'], attacked[p], stats[p]['_vill_alive']) for p in pids})
	exec_parts.append(_minmax(floating, invert=True))
	# Fighting several enemies at once (execution), and being focused by several (execution by how well the
	# player traded then, and a harder survival test).
	multi_raw, focus_raw, focused = several_enemies(A, players, army, villagers, end)
	# As a share of the game from 8:00, so long games don't hand everyone the full bonus.
	fight_minutes = max(1.0, end / 60000 - ARMY_FROM)
	for p in pids:
		stats[p]['several_bonus'] = round(MULTI_BONUS_MAX * min(1.0, multi_raw[p] / fight_minutes / MULTI_FULL)
			+ FOCUS_BONUS_MAX * min(1.0, focus_raw[p] / fight_minutes / FOCUS_FULL), 2)
		stats[p]['multi_enemy_minutes'] = len(focused[p]['multi'])
		stats[p]['focused_minutes'] = len(focused[p]['focused'])
		stats[p]['outnumbered_trade'] = focused[p]['trade']
	playstyle(A, players, stats, army, floating, end, trib_out)
	# Pressure: army on the map 35%, army value sent at the enemy 40%, damage done (enemy army value killed in
	# fights plus enemy villagers killed) 15% (shares of 90%), plus a bonus for forward castles, Donjons and towers.
	# Damage also counts the enemy macro an attack disrupted: production that stalled while the army was in their base.
	disrupted, disruptions = macro_disruption(A, players, vest, timeline_in, into_by, end)
	for p in pids:
		stats[p]['macro_disrupted'] = round(disrupted[p])
		stats[p]['macro_disruptions'] = disruptions[p]
	# ... and enemy Town Centers destroyed, with any research and units in progress in them.
	razed, razings = town_centers_destroyed(A, players, structs, pos, end)
	for p in pids:
		stats[p]['buildings_destroyed'] = round(razed[p])
		stats[p]['buildings_destroyed_list'] = razings[p]
	dealt = {p: army[p]['value_killed_in_fights'] + stats[p]['_vill_kill_value'] + disrupted[p] + razed[p] for p in pids}
	damage = _minmax(dealt)
	# Army on the map only matters if it does damage: scale it by damage done per army value, with full
	# credit at the lobby median or better and nothing for an army that never hurt anyone.
	# Castles and towers are left out of this: their arrows can't be seen as kills, and forward ones are judged
	# by what they did (forward part).
	per_army = {p: dealt[p] / army[p]['avg_units_value_on_map'] for p in pids if army[p]['avg_units_value_on_map']}
	median = sorted(per_army.values())[len(per_army) // 2] if per_army else 0
	for p in pids:
		eff = min(1.0, per_army.get(p, 0) / median) if median else 1.0
		army[p]['damage_efficiency'] = round(eff, 2)
	on_map = _minmax({p: army[p]['avg_units_value_on_map'] * army[p]['damage_efficiency']
		+ (army[p]['avg_value_on_map'] - army[p]['avg_units_value_on_map']) for p in pids})
	# Parts nobody did (no forward castles or towers in the game) are left out and their weight spread over the rest.
	press = {p: _combine([(0.35, None, on_map), (0.40, None, sent), (0.15, None, damage)], p) for p in pids}
	# Forward castles, Donjons and towers only add: up to +1.5, by their cost and effect (see forward_value).
	for p in pids:
		bonus = round(FORWARD_BONUS_MAX * min(1.0, forward_raw[p] / FORWARD_BONUS_FULL), 2)
		stats[p]['forward_bonus'] = bonus
		press[p] = min(10.0, press[p] + bonus)
	# Pressure on each base = enemy army orders aimed into it, plus enemy castles and towers built in it.
	# A castle is only a big threat when an army comes with it: its weight grows with the enemy army
	# orders into that base from 3 minutes before it was placed to 5 minutes after.
	siege_in, cleared, siege_times = Counter(), Counter(), defaultdict(list)
	for c in castles:
		if not c['forward'] or c['status'] == 'unclear':  # only castles that were actually built
			continue
		m0 = int(c['t'] // 60000)
		support = sum(n for m, n in team_into[(players[c['player']]['team_id'], c['base'])].items() if m0 - 3 <= m <= m0 + 5)
		c['army_support'] = support
		c['threat'] = round(10 + min(40, support * 0.5))
		siege_in[c['base']] += c['threat']
		siege_times[c['base']].append(c['t'])
		if c['status'] in ('lost', 'deleted'):
			cleared[c['base']] += c['threat'] / 50
	for a in A:
		if a['a'] == 'BUILD' and a.get('player_id') in players and _name('objects', a['building_id']) in TOWERS:
			b = base_of(a['x'], a['y'])
			if players[b]['team_id'] != players[a['player_id']]['team_id']:
				siege_in[b] += 5
				siege_times[b].append(a['t'])
	pressure = {p: press_in[p] + siege_in[p] for p in pids}

	# Survival only counts for players whose base was really attacked: did they keep their
	# economy at home afterwards, or did they have to rebuild somewhere else?
	max_in = max(pressure.values()) if pressure else 0
	survival, survival_note, surv_detail = {}, {}, {}
	for p in pids:
		enemy_castles = sum(1 for c in castles if c['forward'] and c['base'] == p and c['status'] != 'unclear')
		stats[p]['enemy_castles_in_base'] = enemy_castles
		stats[p]['enemy_castles_cleared'] = sum(1 for c in castles if c['forward'] and c['base'] == p
			and c['status'] in ('lost', 'deleted'))
		if not max_in or (pressure[p] < max(50, 0.25 * max_in) and enemy_castles == 0):
			survival[p] = None
			survival_note[p] = "base not seriously attacked"
			continue
		tl = timeline_in[p]
		total, cum, pressure_start = sum(tl.values()), 0, None
		for minute in sorted(tl):
			cum += tl[minute]
			if cum >= 0.2 * total:
				pressure_start = minute * 60000
				break
		starts = [t for t in [pressure_start] + siege_times[p] if t is not None]
		pressure_start = min(starts) if starts else 0
		eco = [a for a in A if a['a'] == 'BUILD' and a.get('player_id') == p and a['t'] >= pressure_start
			and _name('objects', a['building_id']) in ECO_BUILDINGS]
		home = sum(1 for a in eco if base_of(a['x'], a['y']) == p) / len(eco) if eco else 0.5
		moved_tcs = sum(1 for a in eco if _name('objects', a['building_id']) == 'Town Center' and base_of(a['x'], a['y']) != p)
		# A Town Center next to an ally is an expansion, not a lost base, while the economy stays at home.
		held = max(0.0, min(1.0, home - (0.1 * moved_tcs if home < 0.85 else 0)))
		# Holding a light attack is worth less than holding a heavy one.
		# Being attacked by two or more enemies at once makes holding harder.
		difficulty = min(1.0, pressure[p] / (0.6 * max_in) + min(FOCUS_DIFFICULTY_MAX, FOCUS_DIFFICULTY * len(focused[p]['focused'])))
		value = 1 + held * (4 + 5 * difficulty) + min(cleared[p], 1.0)
		survival[p] = max(1.0, min(10.0, value))
		surv_detail[p] = dict(home=round(home, 2), held=round(held, 2), moved_tcs=moved_tcs, difficulty=round(difficulty, 2),
			cleared=round(min(cleared[p], 1.0), 2), focused=len(focused[p]['focused']), pressure=pressure[p], max_pressure=max_in)
		n_cleared = stats[p]['enemy_castles_cleared']
		cleared_txt = f", cleared {n_cleared} enemy castle{'s' if n_cleared != 1 else ''}" if n_cleared else ""
		n_focus = len(focused[p]['focused'])
		if n_focus:
			cleared_txt += f", attacked by 2+ enemies at once in {n_focus} minute{'s' if n_focus != 1 else ''}"
		level = 'heavy' if difficulty >= 0.66 else 'moderate' if difficulty >= 0.33 else 'light'
		stats[p]['pressure_level'] = level
		survival_note[p] = (
			f"held the base under {level} pressure ({round(home * 100)}% of new economy buildings at home{cleared_txt})" if held >= 0.75 else
			f"base partly lost ({round(home * 100)}% of new economy buildings at home, {moved_tcs} Town Centers rebuilt elsewhere{cleared_txt})"
			if held >= 0.4 else
			f"main base lost ({round(home * 100)}% of new economy buildings at home, {moved_tcs} Town Centers rebuilt elsewhere{cleared_txt})"
		)
		stats[p]['base_lost'] = held < 0.4
	# Who took each lost base: the enemy with the most army orders into it, a castle they built there counting
	# for BASE_CASTLE_ORDERS orders. Saved as a matchup: winner civ and position beat loser civ and position.
	base_kills = []
	for p in pids:
		if not stats[p].get('base_lost'):
			continue
		weight = Counter()
		for e in pids:
			if players[e]['team_id'] != players[p]['team_id']:
				weight[e] = press_matrix[(e, p)] + BASE_CASTLE_ORDERS * sum(
					1 for c in castles if c['player'] == e and c['base'] == p and c['status'] != 'deleted')
		if weight and max(weight.values()) > 0:
			taker = max(weight, key=weight.get)
			base_kills.append(dict(winner=taker, loser=p, share=round(weight[taker] / sum(weight.values()), 2)))
			stats[p]['base_lost_to'] = taker
	team_raw = [{p: trib_out[p] for p in pids}, {p: help_out[p] for p in pids}, {p: stats[p]['flares'] for p in pids}]
	team_parts = [_minmax(raw) for raw in team_raw]  # parts nobody did (no tributes in the game) are left out
	weights = {
		'Flank': dict(exec=.27, press=.36, surv=.27, team=.10),
		'Pocket': dict(exec=.40, press=.25, surv=.25, team=.10),
		None: dict(exec=.32, press=.32, surv=.26, team=.10),
	}
	for p in pids:
		e = min(10.0, _combine(list(zip(EXEC_WEIGHTS, exec_raw, exec_parts)), p) + stats[p]['several_bonus'])
		t = _combine([(.5, team_raw[0], team_parts[0]), (.35, team_raw[1], team_parts[1]), (.15, team_raw[2], team_parts[2])], p)
		surv = round(survival[p], 1) if survival[p] is not None else None
		sc = dict(exec=round(e, 1), press=round(press[p], 1), surv=surv, team=round(t, 1))
		w = dict(weights[players[p]['seat']])
		if surv is None:  # survival was not tested: spread its weight over the rest
			w.pop('surv')
		total_w = sum(w.values())
		sc['overall'] = round(sum(sc[k] * w[k] for k in w) / total_w, 1)
		stats[p]['scores'] = sc
		stats[p]['survival_note'] = survival_note[p]

	# ---- why each score is what it is. The recording is not kept, so this is saved with the game for /explain.
	vill_raw = {p: villager_value(stats[p]['_vill_made'], attacked[p], stats[p]['_vill_alive']) for p in pids}
	on_map_raw = {p: army[p]['avg_units_value_on_map'] * army[p]['damage_efficiency']
		+ (army[p]['avg_value_on_map'] - army[p]['avg_units_value_on_map']) for p in pids}
	exec_info = [
		("Castle Age click", castle_click, mmss, True, False),
		("Imperial Age click", imp_click, mmss, True, False),
		("Villager value", vill_raw, lambda v: f"{v:g}", False, True),
		("First military unit", first_mil, mmss, True, False),
		("Unplanned floating", floating, lambda v: f"{v:g} min", True, True),
	]
	press_info = [
		("Army on the map", on_map_raw, lambda v: f"{v:,.0f} value", 0.35, on_map),
		("Army sent at the enemy", {p: army[p]['value_sent'] for p in pids}, lambda v: f"{v:,.0f} value", 0.40, sent),
		("Damage done", dealt, lambda v: f"{v:,.0f} value", 0.15, damage),
	]
	team_info = [
		("Tributes sent", team_raw[0], lambda v: f"{v:,.0f} resources", 0.5, team_parts[0]),
		("Army orders helping allies", team_raw[1], lambda v: f"{v:,.0f} orders", 0.35, team_parts[1]),
		("Flares", team_raw[2], lambda v: f"{v:,.0f}", 0.15, team_parts[2]),
	]

	def lobby_range(raw, fmt, lower_better):
		known = [v for v in raw.values() if v is not None]
		if not known:
			return None
		best, worst = (min(known), max(known)) if lower_better else (max(known), min(known))
		return f"best {fmt(best)}, worst {fmt(worst)}"

	for p in pids:
		st = stats[p]
		ex_parts = []
		for i, (name, raw, fmt, lower_better, always) in enumerate(exec_info):
			done = always or _did(raw)
			v = raw.get(p)
			part = dict(name=name, value=fmt(v) if v is not None else "never", score=round(exec_parts[i][p], 1) if done else None,
				weight=EXEC_WEIGHTS[i], range=lobby_range(raw, fmt, lower_better))
			if not done:
				part['note'] = "nobody did this, so it is left out"
			if i == 0 and st.get('feudal_units', 0) >= FEUDAL_ARMY_MIN and st.get('feudal_uptime') is not None \
					and exec_parts[0][p] > _minmax(castle_click, invert=True)[p]:
				part['note'] = f"scored by Feudal production uptime ({round(st['feudal_uptime'] * 100)}%) instead, after a {st['feudal_units']}-unit Feudal army"
			if i == 3:
				part['note'] = "counts a quarter as much as the others"
			ex_parts.append(part)
		pr_parts = [dict(name=n, value=f(raw[p]), score=round(sc_[p], 1), weight=w, range=lobby_range(raw, f, False))
			for n, raw, f, w, sc_ in press_info]
		notes = []
		for x in st.get('buildings_destroyed_list', []):
			lost = f" with {', '.join(x['research'])} in progress" if x['research'] else ""
			notes.append(f"{x['value']:,} for {players[x['victim']]['name']}'s Town Center destroyed at {mmss(x['t'])}{lost}")
		if notes:
			pr_parts[2]['note'] = "includes " + "; ".join(notes)
		if st.get('macro_disrupted'):
			raided = sorted({players[x['victim']]['name'] for x in st['macro_disruptions']})
			text = (f"{st['macro_disrupted']:,} of enemy macro disrupted (production that stalled while their army was in "
				f"{', '.join(raided)}'s base)")
			pr_parts[2]['note'] = (pr_parts[2]['note'] + "; " + text) if notes else "includes " + text
		pr_parts[0]['note'] = (f"units counted at {round(army[p]['damage_efficiency'] * 100)}% for the damage they did for their size"
			if army[p]['damage_efficiency'] < 1 else "full credit: the units did at least the lobby's median damage for their size")
		tm_parts = []
		for n, raw, f, w, sc_ in team_info:
			done = _did(raw)
			part = dict(name=n, value=f(raw[p]), score=round(sc_[p], 1) if done else None, weight=w, range=lobby_range(raw, f, False))
			if not done:
				part['note'] = "nobody did this, so it is left out"
			tm_parts.append(part)
		fwd = [x for x in structs if x['player'] == p and x['forward']]
		bonuses = dict(
			several=dict(value=st['several_bonus'], multi=st['multi_enemy_minutes'], focused=st['focused_minutes'],
				trade=st['outnumbered_trade']),
			forward=dict(value=st['forward_bonus'], worth=st.get('forward_value'), effect=st.get('forward_effect'), count=len(fwd),
				built=sum(1 for x in fwd if x['known'])),
		)
		w = dict(weights[players[p]['seat']])
		if st['scores']['surv'] is None:
			w.pop('surv')
		total_w = sum(w.values())
		st['explain'] = dict(
			seat=players[p]['seat'], scores=st['scores'], overall_weights={k: round(v / total_w, 3) for k, v in w.items()},
			exec=ex_parts, press=pr_parts, team=tm_parts, bonuses=bonuses,
			surv=dict(note=survival_note[p], **surv_detail.get(p, {})),
		)

	# ---- performance against BombayBot Elo
	elo_scaled = _minmax({p: players[p]['elo'] for p in pids if players[p]['elo'] is not None})
	by_elo = sorted((p for p in pids if players[p]['elo'] is not None), key=lambda p: -players[p]['elo'])
	by_score = sorted(pids, key=lambda p: -stats[p]['scores']['overall'])
	for p in pids:
		st = stats[p]
		st['score_rank'] = by_score.index(p) + 1
		if p not in elo_scaled:
			st['elo_rank'], st['elo_delta'], st['elo_verdict'] = None, None, None
			continue
		st['elo_rank'] = by_elo.index(p) + 1
		delta = round(st['scores']['overall'] - elo_scaled[p], 1)
		st['elo_delta'] = delta
		st['elo_verdict'] = (
			'well above their Elo' if delta >= 3 else 'above their Elo' if delta >= 1.5 else
			'well below their Elo' if delta <= -3 else 'below their Elo' if delta <= -1.5 else 'around their Elo'
		)

	# ---- strategy names
	for p in pids:
		parts = strategy_parts(stats[p], [c for c in castles if c["player"] == p], A, p, players, base_of, attack_times[p], villagers)
		stats[p]['strategy'] = parts['name']
		stats[p]['strategy_parts'] = parts
		stats[p].update(
			press_out=press_out[p], press_in=press_in[p], pressure_in=pressure[p], help_out=help_out[p],
			trib_out=int(trib_out[p]), trib_in=int(trib_in[p]), peak_bank=peak_bank[p],
			top_target=max(((players[d]['civ'], n) for (a, d), n in press_matrix.items() if a == p), key=lambda x: x[1], default=None),
		)

	# ---- fights: what each side fielded, and how they went against Elo
	fights = fight_records(players, army, A, end)
	for p, (diff, size) in fight_vs_elo(players, fights).items():
		stats[p]['fight_vs_elo'], stats[p]['fight_value'] = diff, size

	return dict(
		duration=end, players=players, stats=stats, castles=castles, water_weight=water, base_kills=base_kills,
		fights=fights,
		tributes=[(a, b, int(v)) for (a, b), v in trib_pairs.most_common()],
		pressure=timeline_in, moments=key_moments(players, stats, castles, {p: t for p, t in resigns.items() if t <= end},
			army, vest, A, end),
	)


FEUDAL_NAMES = {
	'Archer': 'archers', 'Skirmisher': 'skirms', 'Scout Cavalry': 'scouts', 'Militia': 'Men-at-Arms',
	'Spearman': 'spears', 'Eagle Scout': 'Eagles', 'Camel Rider': 'camels', 'Cavalry Archer': 'cavalry archers',
	'Battle Elephant': 'elephants', 'Steppe Lancer': 'lancers',
	'Galley': 'galleys', 'Hulk': 'hulks', 'Fire Galley': 'fire ships', 'Demolition Raft': 'demo ships',
}
NOT_ARMY = (MILITARY_SKIP - {'Monk'}) | {'Trade Cart', 'Trade Cog'}  # monk pushes are a strategy too


def _steady(times, start, end, skip=()):
	""" Share of minutes between start and end with at least one unit queued; minutes in skip don't count. """
	if start is None or end <= start:
		return None
	minutes = [m for m in range(int(start // 60000), int(end // 60000) + 1) if m not in skip]
	if not minutes:
		return None
	busy = {int(t // 60000) for t in times}
	return sum(1 for m in minutes if m in busy) / len(minutes)


def saving_minutes(A, pid, ages):
	"""
	Minutes spent saving for an age-up or an expensive technology (550+ resources): the 2 minutes before
	each click. Not a production gap.
	"""
	clicks = [t for t in ages.values() if t]
	clicks += [a['t'] for a in A if a['a'] == 'RESEARCH' and a.get('player_id') == pid
		and tech_cost(a.get('technology_id')) >= BIG_PURCHASE]
	out = set()
	for t in clicks:
		out.update(range(int((t - SAVE_WINDOW) // 60000), int(t // 60000) + 1))
	return out


def _absolute(share):
	""" A 0..1 share as a 1..10 score on its own scale (not relative to the lobby). """
	return 1.0 if share is None else max(1.0, min(10.0, 10 * share))


def _did(raw):
	""" Did anyone in the lobby do this at all? Parts nobody did are left out of a score. """
	return any(v for v in raw.values() if v is not None)


def _combine(parts, p):
	""" Weighted average of (weight, raw, scaled) parts for player p, skipping parts nobody did. """
	live = [(w, scaled) for w, raw, scaled in parts if raw is None or _did(raw)]
	total = sum(w for w, _ in live)
	return sum(w * scaled[p] for w, scaled in live) / total if total else 5.0


VILLAGER_FULL, VILLAGER_STOP = 120, 150
POP_CAP_NEAR = 190
SAVE_WINDOW = 120000  # resources spent on a big purchase within 2 minutes were being saved on purpose
BIG_PURCHASE = 550    # research, age-up or building costing at least this much (all resources)
NORMAL_FLOAT = 400    # a little bank is normal


ATTACK_MINUTE_ORDERS = 10  # enemy army orders into a base in one minute that count as an attack
TRADE_CART_VALUE = 150  # resources a trade cart stands for in the macro bonus
MACRO_BONUS = 1.0
# Gold shortage without trade: checked over the last 15 minutes analysed, in games of 40+ minutes.
GOLD_CHECK_GAME = 40 * 60000
GOLD_WINDOW = 15 * 60000
LOW_GOLD_SHARE = 0.20      # gold under 20% of what the player spent
MARKET_SOLD_SIGN = 1000    # selling this much at the Market for gold is a sign gold income dried up
CARTS_ENOUGH = 20          # this many trade carts (any time) means the player answered the shortage
GOLD_TRIB_ENOUGH = 2000    # gold tributed in by allies over the window covers the shortage too
NO_TRADE_PENALTY = 1.0
# Buildings that can take a new gold mine: Mining Camp, the Georgian Mule Cart, and the Muisca drop site
# (object 2556, not yet named in the reference data).
MINING_SITES = {'Mining Camp', 'Mule Cart', '2556'}
REPLACE_WINDOW = 10  # minutes: villagers queued past 150 within 10 minutes of an attack on the base are replacements


VILLAGER_TRAIN = 25000     # ms per villager at one Town Center
WORKER_TRAIN = {'Villager': VILLAGER_TRAIN, 'Fishing Ship': 40000}  # fishing ships are economy workers too
VILLAGER_HIT_GRACE = 20000  # ms: a targeted villager its owner ordered again after this got away
VILLAGER_CONFIRM = 3 * 60000  # hits in the last 3 minutes analysed can't be confirmed (no time to see it again)
START_VILLAGERS = {'Chinese': 6, 'Mayans': 4}


def fishing_ship_hits(A, players, villagers, end):
	"""
	Enemy attack orders on fishing ships: {ship id: (time, attacker, victim)} (the last such order).
	Only ships known from their owner's orders count (ordered onto the fish a Dock sends ships to): a ship that
	never got an order can't be seen alive again, so a hit on it can't be told from a kill.
	"""
	dock_ids = {b for a in A if a['a'] == 'DE_QUEUE' and _name('objects', a['unit_id']) == 'Fishing Ship'
		for b in a.get('object_ids') or []}
	fish_ids = {a['target_id'] for a in A if a['a'] in ('GATHER_POINT', 'DE_MULTI_GATHERPOINT')
		and (a.get('target_id') or 0) > 0 and set(a.get('object_ids') or []) & dock_ids}
	ships = {}
	for a in A:
		if a['a'] == 'ORDER' and a.get('target_id') in fish_ids and a.get('player_id') in players:
			for o in a.get('object_ids') or []:
				if o not in villagers:
					ships[o] = a['player_id']
	hits = {}
	for a in A:
		t, att = a.get('target_id') or 0, a.get('player_id')
		if a['a'] == 'ORDER' and a['t'] <= end and t in ships and att in players \
				and players[ships[t]]['team_id'] != players[att]['team_id']:
			hits[t] = (a['t'], att, ships[t])
	return hits


def villager_estimate(A, players, villagers, army_alive, end):
	"""
	Estimated economy workers alive per minute: villagers plus fishing ships. The replay has no unit counts, so:
	- made: each queue click is 1 villager (5 with Shift); with several Town Centers selected each villager
	  goes to the TC with the shortest queue, and a TC trains one every 25 s, so over-queueing is not counted;
	- lost: a villager an enemy attack order targeted, and that its owner never ordered again, is counted as
	  killed (credited to that attacker), except in the last 3 minutes analysed, where that can't be told; deleted villagers are subtracted too. Villagers killed without being
	  clicked on (attack-move, towers, castles) are not seen, so losses are a lower bound;
	- capped by population room: at most 200 minus the army on the map.
	Returns {pid: dict(made=[times], lost=[times], alive=[per minute])}.
	"""
	vill = set(villagers)
	for a in A:  # groups that are mostly known villagers are villagers too
		ids = a.get('object_ids') or []
		if a['a'] in ('ORDER', 'MOVE') and ids and sum(i in vill for i in ids) * 2 >= len(ids):
			vill.update(ids)
	owner, last, deleted = {}, {}, {}
	for a in A:
		if a['t'] > end:
			break
		for o in a.get('object_ids') or []:
			owner.setdefault(o, a.get('player_id'))
			if o not in vill:
				continue
			if a['a'] == 'DELETE':
				deleted[o] = a['t']
			elif a.get('x') and a['x'] > 0:
				last[o] = (a['t'], a['x'], a['y'])
			elif o in last:
				last[o] = (a['t'],) + last[o][1:]
	# Attack orders aimed at a villager: (time, attacker) of the last enemy order that targeted it.
	hits = {}
	for a in A:
		target = a.get('target_id') or 0
		if a['a'] == 'ORDER' and a['t'] <= end and target in vill and a.get('player_id') in players \
				and owner.get(target) in players and owner[target] != a['player_id'] \
				and players[owner[target]]['team_id'] != players[a['player_id']]['team_id']:
			hits[target] = (a['t'], a['player_id'])
	ship_hits = fishing_ship_hits(A, players, vill, end)  # ship id -> (time, attacker, victim)
	own_last = {}
	for a in A:
		for o in a.get('object_ids') or []:
			if a.get('player_id') == owner.get(o):
				own_last[o] = a['t']
	out = {}
	minutes = int(end // 60000) + 1
	for pid, p in players.items():
		made, free, kinds = [], defaultdict(int), Counter()
		busy, first_use = [], {}  # (building, start, end) of each worker trained; first queue per building
		for a in A:
			if a['a'] != 'DE_QUEUE' or a.get('player_id') != pid or a['t'] > end:
				continue
			kind = _name('objects', a['unit_id'])
			if kind not in WORKER_TRAIN:
				continue
			ids = a.get('object_ids') or [0]
			for _ in range(a.get('amount', 1) or 1):
				b = min(ids, key=lambda i: free[i])  # the Town Center (or Dock) with the shortest queue
				first_use.setdefault(b, a['t'])
				busy.append((b, max(a['t'], free[b]), max(a['t'], free[b]) + WORKER_TRAIN[kind]))
				free[b] = max(a['t'], free[b]) + WORKER_TRAIN[kind]
				if free[b] <= end:  # still training when the game was decided: not made
					made.append(free[b])
					kinds[kind] += 1
		made.sort()
		lost = [t for o, t in deleted.items() if owner.get(o) == pid]
		killers = Counter()  # enemy player -> villagers of this player they killed
		killed = []  # (time, {enemy: share}) for each villager counted as killed
		# A villager is killed when an enemy attack order targeted it and its owner never ordered it again.
		for o, (t_hit, attacker) in hits.items():
			if owner.get(o) != pid or o in deleted:
				continue
			if last.get(o, (0,))[0] > t_hit + VILLAGER_HIT_GRACE:
				continue  # it was given orders afterwards: it got away
			if t_hit > end - VILLAGER_CONFIRM:
				continue  # too close to the end to tell: its owner had no time to order it again
			lost.append(t_hit)
			killers[attacker] += 1
			killed.append((t_hit, {attacker: 1.0}, 'villager'))
		# Fishing ships killed: same rule (targeted, never ordered again, not in the last 3 minutes).
		ships_lost = 0
		for o, (t_hit, attacker, victim) in ship_hits.items():
			if victim != pid or own_last.get(o, 0) > t_hit + VILLAGER_HIT_GRACE or t_hit > end - VILLAGER_CONFIRM:
				continue
			lost.append(t_hit)
			ships_lost += 1
			killers[attacker] += 1
			killed.append((t_hit, {attacker: 1.0}, 'fishing ship'))
		lost.sort()
		start = START_VILLAGERS.get(p.get('civ'), 3)
		alive = []
		for m in range(minutes):
			t = m * 60000
			n = start + sum(1 for x in made if x <= t) - sum(1 for x in lost if x <= t)
			army = army_alive[pid][min(len(army_alive[pid]) - 1, m)] if army_alive.get(pid) else 0
			alive.append(max(0, min(n, POP_CAP - army)))
		out[pid] = dict(made=made, lost=lost, alive=alive, villagers=kinds['Villager'], fishing=kinds['Fishing Ship'],
			ships_lost=ships_lost,
			killers=killers, killed=sorted(killed, key=lambda k: k[0]), busy=busy, first_use=first_use)
	return out


VILLAGER_KILL_VALUE = 100  # a villager killed: 50 food plus the gathering it would have done
FEUDAL_ARMY_MIN = 8          # Feudal military units that make a Feudal army opening


def production_uptime(busy, first_use, mil_times, start, end, skip=()):
	"""
	How busy a player's production was between start and end (0..1): the average of Town Center and Dock
	busy time (from the worker simulation) and the share of minutes with a military unit queued.
	"""
	if start is None or end <= start:
		return None
	available = sum(max(0, end - max(start, t0)) for t0 in first_use.values() if t0 < end)
	worked = sum(max(0, min(e, end) - max(s, start)) for _, s, e in busy)
	eco = min(1.0, worked / available) if available else 0.0
	mil = _steady([t for t in mil_times if start <= t <= end], start, end, skip) or 0.0
	return round((eco + mil) / 2, 2)


def villager_value(times, attacked_minutes=(), alive=None):
	"""
	Value of villagers made: full value while the player has under 120 alive, then less and less until 150,
	and nothing at 150+ unless the base was attacked in the previous 10 minutes (replacements). With an
	alive estimate, a villager made after losses counts again because the alive count went down.
	"""
	attacked = set(attacked_minutes)
	value, count = 0.0, 0
	for t in sorted(times):
		count += 1
		if alive:  # villagers alive when this one came out (so replacements after losses count again)
			count = alive[min(len(alive) - 1, int(t // 60000))] + 1
		if count <= VILLAGER_FULL:
			value += 1
		elif count <= VILLAGER_STOP:
			value += (VILLAGER_STOP - count) / (VILLAGER_STOP - VILLAGER_FULL)
		else:
			m = int(t // 60000)
			if any(k in attacked for k in range(m - REPLACE_WINDOW, m + 1)):
				value += 1
	return round(value, 1)


FLOAT_FROM = 10 * 60000
# Execution parts: Castle Age timing, Imperial Age click, villager value, first military unit (quarter weight), floating
EXEC_WEIGHTS = (1.0, 1.0, 1.0, 0.25, 1.0)
# Execution bonus on top: fighting several enemies at once, and holding when focused by several.
# Full bonus at a quarter of the minutes from 8:00 spent clicking 2+ enemies' units (early minutes count more) ...
MULTI_FULL, MULTI_BONUS_MAX = 0.25, 0.75
# ... and at a fifth of them attacked by 2+ enemies with even trades (each minute counts by how the player traded).
FOCUS_FULL, FOCUS_BONUS_MAX = 0.2, 0.75
MULTI_CLICKS = 2          # attack clicks on one enemy's units in a minute that count as fighting them
FOCUS_DIFFICULTY = 0.05   # survival difficulty added per minute attacked by 2+ enemies at once ...
FOCUS_DIFFICULTY_MAX = 0.3  # ... up to this much


DISRUPT_BEFORE = 5 * 60000   # production in the 5 minutes before an attack is the defender's normal
DISRUPT_AFTER = 2 * 60000    # ... compared with during the attack and the 2 minutes after
DISRUPT_GAP = 1              # attacked minutes this far apart are one attack
DISRUPT_MAX = 10 * 60000     # an attack counts its first 10 minutes at most


def _mil_times(A, pid):
	return [a['t'] for a in A if a['a'] == 'DE_QUEUE' and a.get('player_id') == pid
		and _name('objects', a['unit_id']) not in NOT_ARMY and _name('objects', a['unit_id']) not in WORKER_TRAIN]


def macro_disruption(A, players, vest, timeline_in, into_by, end):
	"""
	Enemy macro an attack disrupted. For every attack on a base (minutes with ATTACK_MINUTE_ORDERS+ enemy army
	orders into it, gaps of a minute or less joined, first 10 minutes): the defender's production uptime (Town
	Centers and Docks busy, military queued every minute; military only once they have 120+ villagers) during it and
	the 2 minutes after, against the 5 minutes before. The drop
	times the attack's length times what the defender was spending a minute before it is the resources they did
	not turn into units, research and buildings. It is credited to the attackers by their share of army orders
	into the base during the attack. Returns ({attacker: value}, {attacker: [dict(victim, start, minutes, slip,
	value)]}).
	"""
	out = Counter({p: 0.0 for p in players})
	details = defaultdict(list)
	for d in players:
		minutes = sorted(m for m, n in timeline_in[d].items() if n >= ATTACK_MINUTE_ORDERS)
		episodes, cur = [], []
		for m in minutes:
			if cur and m - cur[-1] > DISRUPT_GAP + 1:
				episodes.append(cur)
				cur = []
			cur.append(m)
		if cur:
			episodes.append(cur)
		if not episodes:
			continue
		mil = _mil_times(A, d)
		for ep in episodes:
			t0, t1 = ep[0] * 60000, min(end, (ep[-1] + 1) * 60000)
			if t0 - DISRUPT_BEFORE < 0:
				continue
			t1 = min(t1, t0 + DISRUPT_MAX)  # a long siege shows in survival and lost bases, not here
			alive = vest[d].get('alive') or []
			full = alive and alive[min(len(alive) - 1, ep[0])] >= VILLAGER_FULL
			if full:  # Town Centers going idle at a full villager count is normal: judge military production only
				before = _steady([t for t in mil if t0 - DISRUPT_BEFORE <= t < t0], t0 - DISRUPT_BEFORE, t0)
				during = _steady([t for t in mil if t0 <= t < t1 + DISRUPT_AFTER], t0, min(end, t1 + DISRUPT_AFTER))
			else:
				before = production_uptime(vest[d]['busy'], vest[d]['first_use'], mil, t0 - DISRUPT_BEFORE, t0)
				during = production_uptime(vest[d]['busy'], vest[d]['first_use'], mil, t0, min(end, t1 + DISRUPT_AFTER))
			if before is None or during is None or before <= during:
				continue
			slip = before - during
			rate = spending_rate(A, d, t0 - DISRUPT_BEFORE, t0)
			length = (t1 - t0) / 60000
			value = slip * length * rate
			orders = {a: sum(into_by[(a, d)].get(m, 0) for m in ep) for a in players
				if players[a]['team_id'] != players[d]['team_id']}
			total = sum(orders.values())
			if not total or value <= 0:
				continue
			for a, n in orders.items():
				if n:
					share = value * n / total
					out[a] += share
					details[a].append(dict(victim=d, start=t0, minutes=round(length), slip=round(slip, 2), value=round(share)))
	return dict(out), {p: details[p] for p in players}


TC_ID = 109
RAZE_HIT_WINDOW = 3 * 60000  # enemy attack clicks on a Town Center up to 3 minutes before its last use ...
RAZE_GONE = 5 * 60000        # ... and never used again for 5+ minutes (or the game ends): it was destroyed
RAZE_IN_PROGRESS = 60000     # units queued in its last minute were lost with it
RAZE_NEAR = 12               # tiles: enemy castles, Donjons and towers this close can have destroyed it
RAZE_STRUCT_CLICKS = 5       # a nearby enemy castle counts like 5 attack clicks when sharing credit


def town_centers_destroyed(A, players, structs, pos, end):
	"""
	Enemy Town Centers destroyed, valued at the Town Center's cost plus research lost with it (an age or tech it
	was researching that the owner researched again later, e.g. Imperial Age) plus units it was making in its last
	minute. A Town Center (anything that queued villagers or an age) counts as destroyed when its owner never used
	it again (5+ minutes before the end) and either research was lost in it, enemies attack-clicked it 2+ times, or
	an enemy attack-clicked it with an enemy castle, Donjon or tower within 12 tiles. Credit is shared by attack
	clicks, a nearby castle or tower counting as 5. Returns ({attacker: value}, {attacker: [details]}).
	"""
	techs = _costs_all().get('techs', {})
	owner, first, last, is_tc = {}, {}, {}, set()
	research = defaultdict(list)   # building -> [(t, tech id)]
	queued = defaultdict(list)     # building -> [(t, unit id, amount)]
	for a in A:
		pid = a.get('player_id')
		if pid not in players or a['a'] not in ('DE_QUEUE', 'RESEARCH'):
			continue
		for o in a.get('object_ids') or []:
			owner.setdefault(o, pid)
			first.setdefault(o, a['t'])
			if a['a'] == 'DE_QUEUE':
				queued[o].append((a['t'], a['unit_id'], a.get('amount', 1) or 1))
				if _name('objects', a['unit_id']) == 'Villager':
					is_tc.add(o)
			else:
				research[o].append((a['t'], a.get('technology_id')))
				if _name('technologies', a.get('technology_id')) in AGES:
					is_tc.add(o)
	for a in A:  # the owner's last use of each building (queues, research, gather points, ungarrison...)
		for o in a.get('object_ids') or []:
			if o in owner and a.get('player_id') == owner[o]:
				last[o] = max(last.get(o, 0), a['t'])
	clicks = defaultdict(Counter)  # building -> attacker -> attack clicks
	for a in A:
		t, att = a.get('target_id') or 0, a.get('player_id')
		if t in is_tc and att in players and players[att]['team_id'] != players[owner[t]]['team_id']:
			if last[t] - RAZE_HIT_WINDOW <= a['t'] <= last[t] + RAZE_HIT_WINDOW:
				clicks[t][att] += 1
	# Where each Town Center stood: the owner's Town Center placement just before it was first used, or their start.
	placed = defaultdict(list)
	for a in A:
		if a['a'] == 'BUILD' and a.get('player_id') in players and _name('objects', a['building_id']) == 'Town Center':
			placed[a['player_id']].append((a['t'], (a['x'], a['y'])))
	out, details = Counter({p: 0.0 for p in players}), defaultdict(list)
	for o in is_tc:
		pid, t_last = owner[o], last[o]
		if end - t_last < RAZE_GONE:
			continue  # still in use near the end
		before = [xy for t, xy in placed[pid] if t <= first[o]]
		where = before[-1] if before and first[o] > 60000 else pos.get(pid)
		credit = Counter(clicks[o])
		n_clicks = sum(clicks[o].values())
		near = False
		if where:
			for x in structs:
				if players[x['player']]['team_id'] != players[pid]['team_id'] and x['t0'] <= t_last <= x['t1'] \
						and math.dist((x['x'], x['y']), where) <= RAZE_NEAR:
					credit[x['player']] += RAZE_STRUCT_CLICKS * x['certainty']
					near = True
		if not credit:
			continue
		lost_research = [(t, tech) for t, tech in research[o] if any(a['a'] == 'RESEARCH' and a.get('player_id') == pid
			and a.get('technology_id') == tech and a['t'] > t_last for a in A)]
		# A Town Center that just stopped being used (villagers full) is not destroyed: it needs research lost in it,
		# 2+ enemy attack clicks, or an attack click with an enemy castle or tower next to it.
		if not (lost_research or n_clicks >= 2 or (n_clicks and near)):
			continue
		value = unit_value(TC_ID)
		value += sum(sum(RESOURCE_WEIGHTS.get(k, 1.0) * v for k, v in techs.get(str(tech), {}).items()) for _, tech in lost_research)
		value += sum(unit_value(u) * n for t, u, n in queued[o] if t_last - RAZE_IN_PROGRESS <= t <= t_last)
		total = sum(credit.values())
		lost_names = [_name('technologies', tech) for _, tech in lost_research]
		for att, n in credit.items():
			details[att].append(dict(victim=pid, t=t_last, value=round(value * n / total), research=lost_names))
			out[att] += value * n / total
	return dict(out), {p: details[p] for p in players}


def several_enemies(A, players, army, villagers, end):
	"""
	From attack clicks on units (an attack order whose target is a unit an enemy commands):
	Units (villagers included) belong to whoever first gave them a move, attack or army command.
	- multi: minutes a player clicked units of 2+ different enemies (MULTI_CLICKS+ each), early minutes
	  weighted like army (x2 before 15:00, x1.5 before 20:00);
	- focused: minutes 2+ different enemies clicked the player's units; each counts by how the player traded
	  in it (army value killed / killed + lost, x2: an even trade against two players is worth full credit,
	  0.5 when no fight losses were seen).
	Returns (multi raw, focus raw, {player: dict(multi=set of minutes, focused=set of minutes, trade=...)}).
	"""
	owner = {}
	for a in A:
		if a.get('player_id') in players and (a['a'] in ARMY_ORDERS or a['a'] in ARMY_COMMANDS):
			for o in a.get('object_ids') or []:
				owner.setdefault(o, a['player_id'])
	clicks = defaultdict(Counter)  # (attacker, victim) -> minute -> clicks
	for a in A:
		att = a.get('player_id')
		t = a.get('target_id') or 0
		if a['a'] != 'ORDER' or att not in players or t <= 0 or a['t'] > end:
			continue
		vic = owner.get(t)
		if vic in players and players[vic]['team_id'] != players[att]['team_id']:
			clicks[(att, vic)][int(a['t'] // 60000)] += 1
	pair = pair_kills(players, army)
	out, multi_raw, focus_raw = {}, {}, {}
	for p in players:
		targets, attackers = defaultdict(set), defaultdict(set)
		for (att, vic), mins in clicks.items():
			for m, n in mins.items():
				if n < MULTI_CLICKS:
					continue
				if att == p:
					targets[m].add(vic)
				if vic == p:
					attackers[m].add(att)
		multi = {m for m, vs in targets.items() if len(vs) >= 2}
		focused = {m for m, xs in attackers.items() if len(xs) >= 2}
		trade = 0.0
		for m in focused:
			killed = sum(pair[(p, e)].get(m, 0) for e in players if (p, e) in pair)
			lost = sum(pair[(e, p)].get(m, 0) for e in players if (e, p) in pair)
			trade += min(2.0, 2 * killed / (killed + lost)) if killed + lost else 0.5
		multi_raw[p] = round(sum(early_weight(m) for m in multi), 1)
		focus_raw[p] = round(trade, 1)
		out[p] = dict(multi=multi, focused=focused, trade=round(trade / len(focused), 2) if focused else None)
	return multi_raw, focus_raw, out


def spending_rate(A, pid, start, end):
	""" Resources spent per minute between start and end: units queued, research, buildings and tributes. """
	if end <= start:
		return 0.0
	costs, techs = _costs(), _costs_all().get('techs', {})
	spent = 0.0
	for a in A:
		if a.get('player_id') != pid or not start <= a['t'] <= end:
			continue
		if a['a'] == 'DE_QUEUE':
			spent += sum(costs.get(str(a['unit_id']), {}).values()) * (a.get('amount', 1) or 1)
		elif a['a'] == 'RESEARCH':
			spent += sum(techs.get(str(a.get('technology_id')), {}).values())
		elif a['a'] == 'BUILD':
			spent += sum(costs.get(str(a.get('building_id')), {}).values())
		elif a['a'] == 'DE_TRIBUTE':
			spent += sum(a.get(k) or 0 for k in ('food', 'wood', 'stone', 'gold'))
	return spent / ((end - start) / 60000)


def unplanned_floating(A, pid, curve, military_alive, st, end):
	"""
	Average resources banked without a reason, from 10 minutes on. Not counted: snapshots where the player
	is near 200 population (estimated villagers alive, army on the map and trade carts), and money
	being saved for a big research, age-up or building started within the next 2 minutes.
	"""
	purchases = []
	vill_queue, trade_queue = [], []
	for a in A:
		if a.get('player_id') != pid:
			continue
		if a['a'] == 'RESEARCH':
			cost = tech_cost(a.get('technology_id'))
			if cost >= BIG_PURCHASE:
				purchases.append((a['t'], cost))
		elif a['a'] == 'BUILD':
			cost = unit_value(a.get('building_id'))
			if cost >= BIG_PURCHASE:
				purchases.append((a['t'], cost))
		elif a['a'] == 'DE_QUEUE':
			n = _name('objects', a['unit_id'])
			if n == 'Villager':
				vill_queue.append((a['t'], a.get('amount', 1) or 1))
			elif n in ('Trade Cart', 'Trade Cog'):
				trade_queue.append((a['t'], a.get('amount', 1) or 1))
	values = []
	for t, _, bank in curve:
		if t < 10 * 60000 or t > end:
			continue
		alive = st.get('_vill_alive')
		if alive:
			villagers = alive[min(len(alive) - 1, int(t // 60000))]
		else:
			villagers = min(VILLAGER_STOP, sum(n for tq, n in vill_queue if tq <= t))
		trade = sum(n for tq, n in trade_queue if tq <= t)
		minute = min(len(military_alive) - 1, int(t // 60000)) if military_alive else 0
		pop = villagers + trade + (military_alive[minute] if military_alive else 0)
		if pop >= POP_CAP_NEAR:
			continue
		planned = max((cost for tp, cost in purchases if t <= tp <= t + SAVE_WINDOW), default=0)
		values.append(max(0, bank - planned - NORMAL_FLOAT))
	return round(sum(values) / len(values)) if values else 0


POP_FROM = 20 * 60000  # population is judged from 20:00 (from 15:00 in shorter games)


def average_population(A, pid, military_alive, end, villagers_alive=None):
	"""
	Estimated population, averaged per minute from 20:00 to the end analysed: villagers alive (estimated),
	trade carts and army on the map, capped at 200. Keeping it high all game is good macro.
	"""
	vill, trade = [], []
	for a in A:
		if a.get('player_id') == pid and a['a'] == 'DE_QUEUE':
			n = _name('objects', a['unit_id'])
			if n == 'Villager':
				vill.append((a['t'], a.get('amount', 1) or 1))
			elif n in ('Trade Cart', 'Trade Cog'):
				trade.append((a['t'], a.get('amount', 1) or 1))
	start = POP_FROM if end >= POP_FROM + 5 * 60000 else 15 * 60000
	values = []
	for minute in range(int(start // 60000), int(end // 60000) + 1):
		t = minute * 60000
		if villagers_alive:
			villagers = villagers_alive[min(len(villagers_alive) - 1, minute)]
		else:
			villagers = min(VILLAGER_STOP, sum(n for tq, n in vill if tq <= t))
		carts = sum(n for tq, n in trade if tq <= t)
		army = military_alive[min(len(military_alive) - 1, minute)] if military_alive else 0
		values.append(min(POP_CAP, villagers + carts + army))
	return round(sum(values) / len(values)) if values else None


def playstyle(A, players, stats, army, floating, end, trib_out):
	"""
	Macro: steady villager and military production, a big army on the map, population kept high from 20:00,
	villagers queued (full value up to 120, less after, none after 150 unless replacing villagers after an
	attack), and little unplanned floating
	(not counted near 200 population or when saving for a big purchase). Bonus of up to 1 point: trade carts
	made and resources tributed to allies. Not trading only costs points (up to 1) when the player ran out of
	gold income late in the game (little gold spent, Market selling or no new Mining Camps) and made no trade
	carts and got no gold from allies; a player still mining gold is never penalised for not trading.
	Micro (estimated; the replay has no kill data): value traded in fights (enemy army value that dropped out
	while fighting this player vs own army value that dropped out while fighting), how much fighting the player
	did, and target picking. Lots of clicking without fighting, or fights that lose the army, score low.
	Both scores are 1-10 within the lobby; 6.0 or more earns the label.
	"""
	pids = list(players)
	vill_times, mil_times = defaultdict(list), defaultdict(list)
	trade = Counter()
	for a in A:
		if a['a'] == 'DE_QUEUE' and a.get('player_id') in players:
			p, n = a['player_id'], _name('objects', a['unit_id'])
			amt = a.get('amount', 1) or 1
			if n in WORKER_TRAIN:  # villagers and fishing ships are economy production
				vill_times[p].append(a['t'])
			elif n in ('Trade Cart', 'Trade Cog'):
				trade[p] += amt
			elif n not in NOT_ARMY:  # warships count; transport ships do not
				mil_times[p].append(a['t'])
	castle_plus = {p: (stats[p]['ages'].get('Castle Age') or end) + 10 * 60000 for p in pids}
	avg_pop = {p: average_population(A, p, army[p]['_alive'], end, stats[p].get('_vill_alive')) for p in pids}
	# Steadiness is scored on its own scale (86% of minutes = 8.6), not stretched across the lobby, so a small
	# gap stays small. Worker production also counts how much was made: more TCs and Docks at once is better.
	worker_end = {p: min(end, castle_plus[p]) for p in pids}
	worker_rate = _minmax({p: sum(1 for t in stats[p].get('_vill_made') or [] if t <= worker_end[p]) / max(1, worker_end[p] / 60000)
		for p in pids})
	macro_parts = [
		{p: 0.5 * _absolute(_steady(vill_times[p], 0, worker_end[p])) + 0.5 * worker_rate[p] for p in pids},
		{p: _absolute(_steady(mil_times[p], stats[p]['first_military'], end, saving_minutes(A, p, stats[p]['ages']))) for p in pids},
		_minmax({p: army[p]['avg_units'] for p in pids}),
		_minmax(avg_pop),
		_minmax({p: villager_value(stats[p].get('_vill_made') or stats[p]['_vill_times'], stats[p]['_attacked'], stats[p].get('_vill_alive')) for p in pids}),
		_minmax(floating, invert=True),
	]
	# Bonus only (never a penalty): trade carts and tributes sent to allies add up to MACRO_BONUS points.
	extra = {p: trade[p] * TRADE_CART_VALUE + trib_out[p] for p in pids}
	top_extra = max(extra.values(), default=0)
	gold_short = gold_shortage(A, players, end)
	for p in pids:
		g = gold_short[p]
		penalty = 0.0
		if g['starved']:
			penalty = NO_TRADE_PENALTY * (1 - min(1.0, trade[p] / CARTS_ENOUGH)) * (1 - min(1.0, g['gold_in'] / GOLD_TRIB_ENOUGH))
		stats[p]['avg_pop'] = avg_pop[p]
		stats[p]['gold_starved'] = g['starved']
		stats[p]['late_gold_share'] = g['share']
		stats[p]['late_market_sold'] = g['sold']
		stats[p]['no_trade_penalty'] = round(penalty, 1)
		stats[p]['trade_carts'] = trade[p]
		stats[p]['macro_bonus'] = round(MACRO_BONUS * extra[p] / top_extra, 1) if top_extra else 0.0
		stats[p]['villager_value'] = villager_value(stats[p].get('_vill_made') or stats[p]['_vill_times'], stats[p]['_attacked'], stats[p].get('_vill_alive'))
	# Micro: value traded in fights. A unit that drops out while its owner was clearly fighting counts as lost
	# in that fight; its value is credited to the enemies fighting that player then, by their share of orders.
	# Conversions look the same as kills here, and count in full.
	lost, killed = Counter(), Counter()
	for p in pids:
		for t_last, value in army[p]['_gone']:
			m = int(t_last // 60000)
			# Only counted when the owner was clearly fighting then (10+ army orders at enemies in a minute):
			# late in the game units also stop being commanded because they sit idle or garrisoned.
			if max(army[p]['_fight_orders'].get(k, 0) for k in (m - 1, m, m + 1, m + 2)) < FIGHT_MIN_ORDERS:
				continue
			lost[p] += value
			credit = Counter()
			for e in pids:
				if players[e]['team_id'] == players[p]['team_id']:
					continue
				for k in (m - 1, m, m + 1, m + 2):
					credit[e] += army[e]['_fights'].get(k, {}).get(p, 0)
			total = sum(credit.values())
			for e, n in credit.items():
				if n:
					killed[e] += value * n / total
	for p in pids:
		killed[p] += stats[p].get('_vill_kill_value', 0)  # enemy villagers killed count as value killed too
	for p in pids:
		army[p]['value_lost_in_fights'] = round(lost[p])
		army[p]['value_killed_in_fights'] = round(killed[p])
		volume = lost[p] + killed[p]
		army[p]['trade_ratio'] = round(killed[p] / lost[p], 2) if lost[p] else (None if not killed[p] else 99.0)
	micro_parts = [
		(0.60, _minmax({p: (killed[p] / (killed[p] + lost[p]) if killed[p] + lost[p] else None) for p in pids})),
		(0.25, _minmax({p: lost[p] + killed[p] for p in pids})),
		(0.15, _minmax({p: army[p]['targeted_share'] for p in pids})),
	]
	for p in pids:
		macro = sum(part[p] for part in macro_parts) / len(macro_parts) + stats[p]['macro_bonus'] - stats[p]['no_trade_penalty']
		macro = round(max(1.0, min(10.0, macro)), 1)
		micro = round(sum(w * part[p] for w, part in micro_parts), 1)
		good_micro = micro >= 6.0 and (army[p]['trade_ratio'] or 0) >= 1.1  # must win its fights on value
		label = ("Macro + Micro" if macro >= 6.0 and good_micro else "Macro" if macro >= 6.0
			else "Micro" if good_micro else "Neither stood out")
		stats[p]['macro'], stats[p]['micro'], stats[p]['playstyle'] = macro, micro, label


def gold_shortage(A, players, end):
	"""
	Did each player run out of gold income late in the game? The replay has no per-resource bank, so this uses
	what the player spent: over the last 15 minutes analysed (games of 40+ minutes only), gold under 20% of
	spending, plus either selling 1000+ at the Market or building no new Mining Camp (or Mule Cart / Muisca
	drop site) in that window.
	"""
	out = {p: dict(starved=False, share=None, sold=0, gold_in=0) for p in players}
	if end < GOLD_CHECK_GAME:
		return out
	t0 = end - GOLD_WINDOW
	costs, techs = _costs(), _costs_all().get('techs', {})
	gold, total, sold, gold_in, camps = Counter(), Counter(), Counter(), Counter(), Counter()
	for a in A:
		if a['t'] < t0 or a['t'] > end:
			continue
		p = a.get('player_id')
		if a['a'] == 'DE_QUEUE' and p in players:
			c, n = costs.get(str(a['unit_id']), {}), a.get('amount', 1) or 1
			gold[p] += c.get('gold', 0) * n
			total[p] += sum(c.values()) * n
		elif a['a'] == 'RESEARCH' and p in players:
			c = techs.get(str(a.get('technology_id')), {})
			gold[p] += c.get('gold', 0)
			total[p] += sum(c.values())
		elif a['a'] == 'BUILD' and p in players:
			c = costs.get(str(a['building_id']), {})
			total[p] += sum(c.values())
			if _name('objects', a['building_id']) in MINING_SITES:
				camps[p] += 1
		elif a['a'] == 'SELL' and p in players:
			sold[p] += 100 * (a.get('amount') or 1)
		elif a['a'] == 'DE_TRIBUTE' and a.get('target_player_id') in players:
			gold_in[a['target_player_id']] += a.get('gold') or 0
	for p in players:
		share = gold[p] / total[p] if total[p] else None
		starved = share is not None and share < LOW_GOLD_SHARE and (sold[p] >= MARKET_SOLD_SIGN or not camps[p])
		out[p] = dict(starved=starved, share=round(share, 2) if share is not None else None, sold=sold[p], gold_in=round(gold_in[p]))
	return out


FIGHT_MIN_ORDERS = 10  # army orders at enemies in a minute that mean a player was clearly fighting
ENGAGE_RADIUS = 12  # tiles
ENGAGE_WINDOW = 60000  # ms


def find_engagements(A, players, base_of, villagers):
	"""
	Army orders that went at the enemy: their base, their units or their buildings.
	Returns {id(action): set of enemy players it went at}.
	"""
	owner = {}
	for a in A:
		if a.get('player_id') in players:
			for o in a.get('object_ids') or []:
				owner.setdefault(o, a['player_id'])
	orders = []
	for a in A:
		pid = a.get('player_id')
		ids = a.get('object_ids') or []
		if a['a'] in ARMY_ORDERS and pid in players and ids and a.get('x') and a['x'] > 0 and a['y'] > 0 \
				and not all(i in villagers for i in ids):
			orders.append(a)
	by_minute = defaultdict(list)
	for a in orders:
		by_minute[int(a['t'] // 60000)].append(a)

	offensive = {}
	for a in orders:
		team = players[a['player_id']]['team_id']
		enemies = set()
		target = a.get('target_id') or 0
		if target > 0 and owner.get(target) in players and players[owner[target]]['team_id'] != team:
			enemies.add(owner[target])
		b = base_of(a['x'], a['y'])
		if players[b]['team_id'] != team:
			enemies.add(b)
		m = int(a['t'] // 60000)
		for other in by_minute[m - 1] + by_minute[m] + by_minute[m + 1]:
			if players[other['player_id']]['team_id'] != team and abs(other['t'] - a['t']) <= ENGAGE_WINDOW \
					and math.dist((other['x'], other['y']), (a['x'], a['y'])) <= ENGAGE_RADIUS:
				enemies.add(other['player_id'])
		if enemies:
			offensive[id(a)] = enemies
	return offensive


# How much warships count in pressure, by map (names are matched without spaces or symbols). On lake maps
# water is worth holding but gives no edge on land; on island maps water is the game. Other maps: 0.6.
WATER_IMPORTANCE = (
	(('island', 'archipelago', 'waternomad', 'migration', 'pacific'), 1.0),
	(('baltic', 'mediterranean', 'coastal', 'continental', 'scandinavia', 'rivers', 'nile', 'sea'), 0.7),
	(('fourlakes', '4lakes', 'goldenlakes', 'kawasan', 'lake'), 0.4),
)
WATER_DEFAULT = 0.6


def water_weight(map_name):
	name = re.sub(r'[^0-9a-z]', '', (map_name or '').lower())
	for keys, weight in WATER_IMPORTANCE:
		if any(k in name for k in keys):
			return weight
	return WATER_DEFAULT


EARLY_DOUBLE = 15  # minutes: army counts double before this
EARLY_HALF = 20    # and 1.5 times before this
ARMY_FROM = 8  # army on the map is averaged from minute 8


def early_weight(minute, imp_at=None):
	""" Early army counts more (on the map and sent at the enemy): x2 before 15:00, x1.5 before 20:00, x1 after. """
	if minute < EARLY_DOUBLE:
		return 2.0
	if minute < EARLY_HALF:
		return 1.5
	return 1.0


STRUCT_SAME_TILES = 3        # placements this close ...
STRUCT_SAME_MS = 60000       # ... and this soon after another by the same player are the same building
UNSEEN_CERTAINTY = 0.5       # a castle placement never seen built (no units, research or hits) counts half
FORWARD_RADIUS = 15          # tiles around a forward building whose activity shows who holds the area
FORWARD_BEFORE = 5 * 60000   # compared with the 5 minutes before it was placed
FORWARD_AFTER = 15 * 60000   # over up to 15 minutes after (while it stands)
FORWARD_MIN_ACTIVITY = 5     # less activity than this nearby says nothing about control
FORWARD_FLOOR = 0.25         # a forward building that did nothing still counts this much of its cost
CASTLE_ID = 82
FORWARD_BONUS_MAX = 1.5    # pressure bonus for forward buildings ...
FORWARD_BONUS_FULL = 3.0   # ... in full at 3 Castles' worth (cost x effect, see forward_value)
FORWARD_PARTS = dict(held=0.25, area=0.20, ground=0.15, push=0.20, trade=0.20)
FORWARD_PUSH_RADIUS = 20     # tiles: siege pushing in under the building's cover
FORWARD_PUSH_FULL = 15       # building attacks / attack-ground orders in its first 15 minutes for full push credit
FORWARD_TRADE_RADIUS = 10    # tiles from the enemy trade route
FORWARD_TRADE_FULL = 30      # enemy trade carts made while it stood for full trade credit
TRADE_ROUTE_MIN = 40         # tiles between two Markets for a trade route


def structure_list(A, players, castles, base_of, end):
	"""
	Castles (Donjons, Kreposts...) and towers as buildings, not placements: repeated placements by the same
	player at the same spot within a minute, and castle placements matched to an object already counted, are
	dropped. Each: player, kind, building_id, x, y, t0 (placed), t1 (lost / deleted / end), known (seen built:
	units, research or enemy hits), certainty (1, or 0.5 when never seen built), forward and base.
	Towers are not tracked one by one (they produce nothing), so they stand until the end and count as known.
	"""
	out, seen_oids = [], set()

	def repeat(pid, x, y, t):
		return any(o['player'] == pid and abs(o['t0'] - t) <= STRUCT_SAME_MS and math.dist((o['x'], o['y']), (x, y)) <= STRUCT_SAME_TILES
			for o in out)

	for c in sorted(castles, key=lambda c: c['t']):
		if c.get('oid') and c['oid'] in seen_oids:
			continue
		if repeat(c['player'], c['x'], c['y'], c['t']):
			continue
		if c.get('oid'):
			seen_oids.add(c['oid'])
		known = c['status'] in ('up', 'lost', 'deleted')
		t1 = c.get('lost_at') if c['status'] == 'lost' else c.get('deleted_at') if c['status'] == 'deleted' else end
		out.append(dict(player=c['player'], kind=c['kind'], building_id=c['building_id'], x=c['x'], y=c['y'], t0=c['t'],
			t1=t1 or end, known=known, certainty=1.0 if known else UNSEEN_CERTAINTY, forward=c['forward'], base=c['base']))
	for a in A:
		if a['a'] != 'BUILD' or a.get('player_id') not in players or _name('objects', a['building_id']) not in TOWERS:
			continue
		pid = a['player_id']
		if repeat(pid, a['x'], a['y'], a['t']):
			continue
		where = base_of(a['x'], a['y'])
		out.append(dict(player=pid, kind=_name('objects', a['building_id']), building_id=a['building_id'], x=a['x'], y=a['y'],
			t0=a['t'], t1=end, known=True, certainty=1.0, tower=True, base=where,
			forward=where != pid and players[where]['team_id'] != players[pid]['team_id']))
	return out


def _dist_to_segment(p, a, b):
	ax, ay = a
	bx, by = b
	dx, dy = bx - ax, by - ay
	if dx == dy == 0:
		return math.dist(p, a)
	t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
	return math.dist(p, (ax + t * dx, ay + t * dy))


def forward_value(A, players, structs, villagers, end):
	"""
	{player: forward building value} in Castles' worth: each forward building counts its cost (a Castle = 1,
	a Donjon about 0.33, a tower about 0.24) times FORWARD_FLOOR + the rest by its effect (0..1):
	- held (0.25): how long it stood, as a share of the rest of the game (castles; towers are not tracked);
	- area (0.20): after it went up, the owner's team's share of the activity within 15 tiles (buildings placed
	  count 3, army orders 1): did they hold the position;
	- ground (0.15): how much that share rose compared with the 5 minutes before (doubled, up to 1);
	- push (0.20): the owner's team attacking enemy buildings or using attack-ground (rams, trebuchets, bombards,
	  mangonels) within 20 tiles in its first 15 minutes standing: it covered siege pushing in (full at 15);
	- trade (0.20): it stood within 10 tiles of the enemy team's trade route (the line between their Markets 40+
	  tiles apart) while they made trade carts (full at 30 carts): it denied trade or made it costly. Left out
	  (weight spread over the rest) when that team made no trade carts while it stood.
	Never-seen-built placements count half. Writes effect and its parts into each building.
	"""
	castle_value = unit_value(CASTLE_ID) or 780
	events = []  # (t, x, y, player, weight)
	for a in A:
		pid = a.get('player_id')
		if pid not in players or a.get('x') is None or a['x'] <= 0:
			continue
		if a['a'] == 'BUILD':
			events.append((a['t'], a['x'], a['y'], pid, 3))
		elif a['a'] in ARMY_ORDERS:
			ids = a.get('object_ids') or []
			if ids and not all(i in villagers for i in ids):
				events.append((a['t'], a['x'], a['y'], pid, 1))
	# Enemy buildings: anything that queued units or research belongs to that player.
	building_owner = {}
	for a in A:
		if a['a'] in ('DE_QUEUE', 'RESEARCH') and a.get('player_id') in players:
			for o in a.get('object_ids') or []:
				building_owner.setdefault(o, a['player_id'])
	siege_orders = []  # (t, x, y, player): attacks on buildings and attack-ground
	for a in A:
		pid = a.get('player_id')
		if pid not in players or a.get('x') is None or a['x'] <= 0:
			continue
		target = a.get('target_id') or 0
		if a['a'] == 'ATTACK_GROUND' or (a['a'] == 'ORDER' and target in building_owner
				and players[building_owner[target]]['team_id'] != players[pid]['team_id']):
			siege_orders.append((a['t'], a['x'], a['y'], pid))
	markets = [(a['t'], a['x'], a['y'], a['player_id']) for a in A if a['a'] == 'BUILD' and a.get('player_id') in players
		and _name('objects', a['building_id']) == 'Market']
	carts = [(a['t'], a['player_id'], a.get('amount', 1) or 1) for a in A if a['a'] == 'DE_QUEUE'
		and a.get('player_id') in players and _name('objects', a['unit_id']) in ('Trade Cart', 'Trade Cog')]

	out = Counter({p: 0.0 for p in players})
	for x in structs:
		if not x['forward']:
			continue
		team = players[x['player']]['team_id']
		t0, t1 = x['t0'], x['t1']
		here = (x['x'], x['y'])
		stand_end = max(t0 + 60000, t1)

		def control(lo, hi):
			own = other = 0
			for t, ex, ey, pid, w in events:
				if lo <= t < hi and math.dist((ex, ey), here) <= FORWARD_RADIUS:
					if players[pid]['team_id'] == team:
						own += w
					else:
						other += w
			return own / (own + other) if own + other >= FORWARD_MIN_ACTIVITY else None

		before = control(t0 - FORWARD_BEFORE, t0)
		after = control(t0, max(t0 + 60000, min(t1, t0 + FORWARD_AFTER)))
		presence = after if after is not None else 0.5
		gain = max(0.0, presence - (before if before is not None else 0.5))
		parts = {'area': presence, 'ground': min(1.0, 2 * gain)}
		if x['known'] and not x.get('tower'):
			parts['held'] = max(0.0, min(1.0, (t1 - t0) / max(60000, end - t0)))
		push_end = min(stand_end, t0 + FORWARD_AFTER)
		pushes = sum(1 for t, ex, ey, pid in siege_orders if t0 <= t < push_end and players[pid]['team_id'] == team
			and math.dist((ex, ey), here) <= FORWARD_PUSH_RADIUS)
		x['pushes'] = pushes
		parts['push'] = min(1.0, pushes / FORWARD_PUSH_FULL)
		enemy_carts = sum(n for t, pid, n in carts if t0 <= t < stand_end and players[pid]['team_id'] != team)
		if enemy_carts:
			theirs = [(mx, my) for t, mx, my, pid in markets if t < stand_end and players[pid]['team_id'] != team]
			near = any(_dist_to_segment(here, m1, m2) <= FORWARD_TRADE_RADIUS
				for i, m1 in enumerate(theirs) for m2 in theirs[i + 1:] if math.dist(m1, m2) >= TRADE_ROUTE_MIN)
			parts['trade'] = min(1.0, enemy_carts / FORWARD_TRADE_FULL) if near else 0.0
		weights = {k: FORWARD_PARTS[k] for k in parts}
		effect = sum(weights[k] * parts[k] for k in parts) / sum(weights.values())
		weight = unit_value(x['building_id']) / castle_value * x['certainty']
		x.update(effect=round(effect, 2), weight=weight, hold=round(parts['held'], 2) if 'held' in parts else None,
			presence=round(presence, 2), gain=round(gain, 2), push=round(parts['push'], 2),
			trade=round(parts['trade'], 2) if 'trade' in parts else None)
		out[x['player']] += weight * (FORWARD_FLOOR + (1 - FORWARD_FLOOR) * effect)
	return dict(out)


def army_presence(A, players, base_of, villagers, end, offensive, imp_at=None, water=1.0, structs=None):
	"""
	Estimate each player's army on the map over time from the unit ids in their commands.
	A unit is military if it ever gets an army-only command (formation, stance, patrol, attack-move...)
	or is sent into a non-allied area; it counts as alive from its first command until 90 s after its last.
	Each unit is valued at the player's average military unit cost so far (gold weighted most).
	"""
	pids = list(players)
	minutes = int(end // 60000) + 1
	seen, military = {}, defaultdict(set)
	for a in A:
		pid = a.get('player_id')
		ids = [i for i in (a.get('object_ids') or []) if i not in villagers]
		if pid not in players or not ids:
			continue
		is_army = a['a'] in ARMY_COMMANDS or id(a) in offensive
		for i in ids:
			first_last = seen.setdefault((pid, i), [a['t'], a['t']])
			first_last[1] = a['t']
			if is_army:
				military[pid].add(i)

	# Average military unit value per minute, from what the player had queued so far.
	queued = defaultdict(list)
	for a in A:
		if a['a'] == 'DE_QUEUE' and a.get('player_id') in players:
			name = _name('objects', a['unit_id'])
			if name not in NOT_ARMY and name != 'Villager':
				queued[a['player_id']].append((a['t'], unit_value(a['unit_id']), a.get('amount', 1) or 1, name in WARSHIPS))

	out = {}
	for pid in pids:
		alive = [0] * minutes
		for i in military[pid]:
			first, last = seen[(pid, i)]
			for m in range(int(first // 60000), min(minutes - 1, int(min(end, last + ARMY_ALIVE_GRACE) // 60000)) + 1):
				alive[m] += 1
		alive = [min(n, POP_CAP) for n in alive]  # an estimate can never beat the population cap
		value_per_unit, total, count, q = [0.0] * minutes, 0.0, 0, sorted(queued[pid])
		press_value = [0.0] * minutes  # the same, with warships discounted by how much water matters on this map
		ships = 0.0
		j = 0
		for m in range(minutes):
			while j < len(q) and q[j][0] <= (m + 1) * 60000:
				total += q[j][1] * q[j][2]
				count += q[j][2]
				if q[j][3]:
					ships += q[j][1] * q[j][2]
				j += 1
			value_per_unit[m] = total / count if count else 0.0
			press_value[m] = value_per_unit[m] * (1 - (ships / total if total else 0) * (1 - water))
		structures = [0.0] * minutes
		if structs is not None:  # castles count while they stand; placements never seen built count half
			for x in structs:
				if x['player'] == pid:
					for m in range(int(x['t0'] // 60000) + 1, min(minutes, int(x['t1'] // 60000) + 1)):
						structures[m] += unit_value(x['building_id']) * x['certainty']
		else:
			for a in A:
				if a['a'] == 'BUILD' and a.get('player_id') == pid and _name('objects', a['building_id']) in CASTLES | TOWERS:
					for m in range(int(a['t'] // 60000) + 1, minutes):
						structures[m] += unit_value(a['building_id'])
		sent = set()
		for a in A:
			if a.get('player_id') == pid and id(a) in offensive:
				for i in a.get('object_ids') or []:
					if i in military[pid]:
						sent.add((i, int(a['t'] // 60000)))  # each unit once per minute
		window = range(min(ARMY_FROM, minutes - 1), minutes)  # from 8:00 (Feudal armies count)
		# A small army early is worth a lot: x2 before 15:00, x1.5 before 20:00 (same as army sent)
		my_imp = imp_at.get(pid) if isinstance(imp_at, dict) else imp_at
		on_map = [(alive[m] * press_value[m] + structures[m]) * early_weight(m, my_imp) for m in window]
		units_on_map = [alive[m] * press_value[m] * early_weight(m, my_imp) for m in window]
		# Micro inputs: how much the player controls the army, picks targets, splits groups and keeps units alive.
		army_cmds = [a for a in A if a.get('player_id') == pid and any(i in military[pid] for i in (a.get('object_ids') or []))]
		army_minutes = max(1.0, (end - min((seen[(pid, i)][0] for i in military[pid]), default=end)) / 60000)
		targeted = sum(1 for a in army_cmds if id(a) in offensive and (a.get('target_id') or 0) > 0)
		offensive_cmds = sum(1 for a in army_cmds if id(a) in offensive)
		group_sizes = [len(a.get('object_ids') or []) for a in army_cmds if a['a'] in ARMY_ORDERS]
		produced = [0] * minutes
		cum = 0
		j = 0
		for m in range(minutes):
			while j < len(q) and q[j][0] <= (m + 1) * 60000:
				cum += q[j][2]
				j += 1
			produced[m] = cum
		kept = [alive[m] / produced[m] for m in window if produced[m] >= 10]
		# Units that stop being commanded before the end of the game are presumed gone (most often killed).
		gone = []
		for i in military[pid]:
			t_gone = seen[(pid, i)][1] + ARMY_ALIVE_GRACE
			if t_gone < end - ARMY_ALIVE_GRACE:
				gone.append((seen[(pid, i)][1], value_per_unit[min(minutes - 1, int(seen[(pid, i)][1] // 60000))]))
		# minute -> enemy -> orders. An order that went at several enemies (stacked armies) is split between
		# them, so it is not counted in full against each one.
		fights = defaultdict(Counter)
		fight_orders = Counter()  # minute -> army orders at any enemy
		for a in army_cmds:
			enemies = offensive.get(id(a), ())
			if enemies:
				fight_orders[int(a['t'] // 60000)] += 1
			for e in enemies:
				fights[int(a['t'] // 60000)][e] += 1 / len(enemies)
		out[pid] = dict(
			_gone=gone, _fights=fights, _fight_orders=fight_orders, _alive=alive,
			army_apm=round(len(army_cmds) / army_minutes, 1),
			targeted_share=round(targeted / offensive_cmds, 2) if offensive_cmds else 0.0,
			avg_group=round(sum(group_sizes) / len(group_sizes), 1) if group_sizes else None,
			units_kept=round(sum(kept) / len(kept), 2) if kept else None,
			avg_units=round(sum(alive[m] for m in window) / len(window)),
			peak_units=max(alive) if alive else 0,
			avg_value_on_map=round(sum(on_map) / len(on_map)),
			avg_units_value_on_map=round(sum(units_on_map) / len(units_on_map)),
			unit_value=round(value_per_unit[-1]),
			value_sent=round(sum(press_value[m] * early_weight(m, (imp_at or {}).get(pid)) for _, m in sent)),
			units_by_5min=[alive[m] for m in range(0, minutes, 5)],
		)
	return out


def _army_between(A, pid, t0, t1):
	""" Units queued by a player from t0 up to (not including) t1, by line. """
	c = Counter()
	for a in A:
		if a['a'] == 'DE_QUEUE' and a.get('player_id') == pid and t0 <= a['t'] < t1:
			n = _name('objects', a['unit_id'])
			if n not in NOT_ARMY:
				c[n] += a.get('amount', 1) or 1
	return c


SIEGE = {'Battering Ram', 'Mangonel', 'Scorpion', 'Bombard Cannon', 'Trebuchet', 'Siege Tower',
	'Rocket Cart', 'Organ Gun', 'Houfnice', 'Traction Trebuchet', 'Armored Elephant', 'Siege Elephant'}


def _top_lines(counter, minimum, names=LINE_NAMES, limit=2):
	merged = Counter()  # upgrades of one line (Galley, War Galley) count together
	for n, c in counter.items():
		merged[names.get(n, n)] += c
	return [label for label, c in merged.most_common() if c >= minimum][:limit]


def _army_lines(counter, minimum=5):
	""" Top two unit lines, plus any siege the player made (2 or more) that isn't already listed. """
	lines = _top_lines(counter, minimum)
	for n, c in counter.most_common():
		label = LINE_NAMES.get(n, n)
		if n in SIEGE and c >= 2 and label not in lines:
			lines.append(label)
	return lines


FEUDAL_ECO_TECHS = {'Double-Bit Axe', 'Horse Collar', 'Gold Mining', 'Stone Mining', 'Wheelbarrow', 'Gillnets'}
FEUDAL_BOOM_FARMS = 8
FISH_BOOM_SHIPS = 5  # working fishing ships up to 10 minutes after Castle Age


def working_fishing_ships(A, pid, until, villagers=()):
	"""
	(fishing ships made, fishing ships that went to work) up to `until`. The replay doesn't say which unit is
	a fishing ship, so a ship counts as working when its Dock's gather point was on a fish when it came out
	(new ships go straight to fishing), or when the player ordered units onto fish (the fish a Dock gather
	point was set on, by anyone). Ships sent to a spot on the water and never ordered stay idle.
	"""
	gather = defaultdict(list)  # dock -> [(t, on a resource)]
	fish_ids = set()
	for a in A:
		if a['a'] in ('GATHER_POINT', 'DE_MULTI_GATHERPOINT'):
			target = a.get('target_id') or -1
			for b in a.get('object_ids') or []:
				gather[b].append((a['t'], target > 0))
			if target > 0:
				fish_ids.add(target)  # resources Docks send ships to (fish); refined below to this player's docks
	docks, made, auto = set(), 0, 0
	free = defaultdict(int)
	for a in A:
		if a['a'] != 'DE_QUEUE' or a.get('player_id') != pid or a['t'] > until or _name('objects', a['unit_id']) != 'Fishing Ship':
			continue
		ids = a.get('object_ids') or [0]
		docks.update(ids)
		for _ in range(a.get('amount', 1) or 1):
			b = min(ids, key=lambda i: free[i])
			free[b] = max(a['t'], free[b]) + WORKER_TRAIN['Fishing Ship']
			if free[b] > until:
				continue
			made += 1
			points = [on for t, on in gather.get(b, []) if t <= free[b]]
			if points and points[-1]:
				auto += 1
	dock_fish = {a.get('target_id') for a in A if a['a'] in ('GATHER_POINT', 'DE_MULTI_GATHERPOINT')
		and (a.get('target_id') or -1) > 0 and set(a.get('object_ids') or []) & docks} or fish_ids
	ordered = set()
	for a in A:
		if a['a'] == 'ORDER' and a.get('player_id') == pid and a['t'] <= until and a.get('target_id') in dock_fish:
			ordered.update(i for i in a.get('object_ids') or [] if i not in villagers and i not in docks)
	return made, min(made, auto + len(ordered))


def strategy_parts(st, my_castles, A, pid, players, base_of, attack_times, villagers=()):
	""" The strategy as separate parts (for stats later) plus a readable name. """
	ages = st['ages']
	feudal, castle, imp = (ages.get(a) for a in AGES)
	end = max((a['t'] for a in A), default=0) + 1
	team = players[pid]['team_id']

	# Feudal Age: a tower rush means towers in an enemy base, not defensive towers at home.
	forward_towers = 0
	if feudal:
		forward_towers = sum(1 for a in A if a['a'] == 'BUILD' and a.get('player_id') == pid
			and _name('objects', a['building_id']) in TOWERS and feudal <= a['t'] < (castle or end)
			and players[base_of(a['x'], a['y'])]['team_id'] != team)
	feudal_army = _army_between(A, pid, feudal, castle or end) if feudal else Counter()
	feudal_units = _top_lines(feudal_army, 3, FEUDAL_NAMES)
	feudal_attacks = sum(1 for t in attack_times if feudal and feudal <= t < (castle or end))
	feudal_attack = feudal_attacks >= 10

	# Feudal boom (Malay-style): little army in Feudal, eco upgrades and farms (or an extra TC) instead.
	feudal_eco = {_name('technologies', a['technology_id']) for a in A if a['a'] == 'RESEARCH'
		and a.get('player_id') == pid and feudal and feudal <= a['t'] < (castle or end)} & FEUDAL_ECO_TECHS
	feudal_builds = Counter(_name('objects', a['building_id']) for a in A if a['a'] == 'BUILD'
		and a.get('player_id') == pid and feudal and feudal <= a['t'] < (castle or end))
	feudal_boom = bool(feudal) and sum(feudal_army.values()) < FEUDAL_ARMY_MIN and len(feudal_eco) >= 2 \
		and (feudal_builds['Farm'] >= FEUDAL_BOOM_FARMS or feudal_builds['Town Center'] >= 1)

	if forward_towers >= 2:
		opening = "Feudal tower rush"
	elif sum(feudal_army.values()) >= 8:
		ships = sum(n for u, n in feudal_army.items() if u in WARSHIPS)
		if ships * 2 >= sum(feudal_army.values()):  # mostly warships: a water opening (sea fights count as attacks)
			opening = "Feudal water: " + " + ".join(feudal_units or ["warships"])
		else:
			opening = "Feudal " + " + ".join(feudal_units or ["army"]) + ("" if feudal_attack else " (defensive)")
	elif feudal_boom:
		opening = "Feudal boom"
	elif feudal and castle and castle - feudal <= 4 * 60000:
		opening = "Fast Castle"
	else:
		opening = "Standard"
	boom = bool(castle and st.get('tc_after_castle', 0) >= 2)  # TC boom: 2+ Town Centers soon after Castle Age
	# Fish boom: 5+ fishing ships that went to work (not left idle), up to 10 minutes after Castle Age.
	boom_end = (castle or end) + 10 * 60000
	fish, fish_working = working_fishing_ships(A, pid, boom_end, villagers)
	fish_boom = fish_working >= FISH_BOOM_SHIPS
	# Named by the economy behind it: extra Town Centers on farms, fishing ships, or both.
	boom_label = "farm + fish boom" if boom and fish_boom else "fish boom" if fish_boom else "farm boom" if boom else ""

	# Castle Age and Imperial Age armies, separately.
	castle_units = _army_lines(_army_between(A, pid, castle, imp or end)) if castle else []
	imperial_units = _army_lines(_army_between(A, pid, imp, end)) if imp else []
	main_units = castle_units or imperial_units
	fast_imp = bool(imp and imp < 25 * 60000)
	# Castles seen built (units, research or enemy hits), each object once: not every placement.
	forward_castles = len({c.get('oid') for c in my_castles if c['forward'] and c['status'] != 'unclear' and c.get('oid')})
	tower_creep = st['towers'] >= 8

	parts = []
	# Opening, then the boom as its own stage: "Feudal scouts + spears → farm + fish boom → Knights".
	if opening != "Standard":
		parts.append(opening)
	if boom_label:
		parts.append(boom_label)
	if castle_units:
		parts.append(" + ".join(castle_units))
	if imperial_units and imperial_units != castle_units:
		parts.append("Imp: " + " + ".join(imperial_units))
	extras = []
	if fast_imp:
		extras.append("fast Imperial")
	if forward_castles:
		extras.append(f"{forward_castles} forward castle{'s' if forward_castles > 1 else ''}")
	if tower_creep:
		extras.append("tower creep")
	if extras:
		parts.append(", ".join(extras))
	return dict(
		name=" → ".join(parts) if parts else "Standard play",
		opening=opening, feudal_boom=feudal_boom, boom=boom, fish_boom=fish_boom, fishing_ships=fish,
		fishing_ships_working=fish_working, feudal_units=feudal_units, feudal_attack=feudal_attack,
		forward_towers=forward_towers, castle_units=castle_units, imperial_units=imperial_units,
		main_units=main_units, fast_imp=fast_imp, forward_castles=forward_castles, tower_creep=tower_creep,
	)


BASE_CASTLE_ORDERS = 50  # a castle in a base counts like this many army orders into it when crediting a lost base
MOMENTS_MAX = 6  # the 5 biggest events plus the resignation that ends the analysis
BATTLE_MIN = 500      # army value killed (both players) in 3 minutes that makes a fight worth listing
RAID_MIN = 3          # villagers of one player killed by one raider in one burst that make a raid worth listing
RAID_VILLAGER_WEIGHT = 300  # a villager killed counts like this much army value when picking moments
RAID_GAP = 60000      # kills more than a minute apart are separate raids
LATE_WINDOW = 3 * 60000  # events this close to the end of the analysis count half when picking moments
ELIMINATED_GAP = 3 * 60000  # stopped giving orders this long before the end without resigning


EPISODE_GAP = 5 * 60000  # fights or raids between the same two players less than 5 minutes apart are one moment


def _episodes(items, time_of):
	""" Split time-sorted items into runs whose consecutive starts are less than EPISODE_GAP apart. """
	out, cur = [], []
	for it in items:
		if cur and time_of(it) - time_of(cur[-1]) > EPISODE_GAP:
			out.append(cur)
			cur = []
		cur.append(it)
	if cur:
		out.append(cur)
	return out


def pair_kills(players, army):
	"""
	(killer, victim) -> minute -> army value killed. A unit lost while its owner was clearly fighting is credited
	to the enemies fighting that player then, by their share of orders.
	"""
	team_of = {p: players[p]['team_id'] for p in players}
	pair = defaultdict(Counter)
	for p in players:
		for t_last, value in army[p]['_gone']:
			m = int(t_last // 60000)
			ks = (m - 1, m, m + 1, m + 2)
			if max(army[p]['_fight_orders'].get(k, 0) for k in ks) < FIGHT_MIN_ORDERS:
				continue
			credit = Counter()
			for e in players:
				if team_of[e] != team_of[p]:
					for k in ks:
						credit[e] += army[e]['_fights'].get(k, {}).get(p, 0)
			total = sum(credit.values())
			for e, n in credit.items():
				if n:
					pair[(e, p)][m] += value * n / total
	return pair


def fight_episodes(players, army):
	"""
	Major fights per pair of players: 3-minute windows with at least BATTLE_MIN army value killed, windows of a
	pair less than 5 minutes apart merged into one fight. Each: a, b, a_killed (value a killed of b), b_killed,
	start_m, end_m (minutes) and windows.
	"""
	pair = pair_kills(players, army)
	fights = []
	for a, b in {tuple(sorted(k)) for k in pair}:
		for m in sorted(set(pair[(a, b)]) | set(pair[(b, a)])):
			ka = sum(pair[(a, b)].get(k, 0) for k in (m, m + 1, m + 2))
			kb = sum(pair[(b, a)].get(k, 0) for k in (m, m + 1, m + 2))
			fights.append((ka + kb, m, a, b, ka, kb))
	used, per_pair = defaultdict(set), defaultdict(list)
	for size, m, a, b, ka, kb in sorted(fights, key=lambda f: -f[0]):
		if size < BATTLE_MIN or used[(a, b)] & {m, m + 1, m + 2}:
			continue
		used[(a, b)].update(range(m - 2, m + 3))
		per_pair[(a, b)].append((m, ka, kb))
	out = []
	for (a, b), windows in per_pair.items():
		for episode in _episodes(sorted(windows), lambda w: w[0] * 60000):
			out.append(dict(a=a, b=b, a_killed=sum(w[1] for w in episode), b_killed=sum(w[2] for w in episode),
				start_m=episode[0][0], end_m=episode[-1][0], windows=len(episode)))
	return sorted(out, key=lambda f: f['start_m'])


FIGHT_ARMY_BEFORE = 5  # minutes: the units a player queued this long before a fight (and during it) fought in it


def _side(players, army, p, enemy, start_m, end_m):
	"""
	p and the teammates who clearly fought the same enemy during the fight (FIGHT_MIN_ORDERS orders at them in
	some minute of it), so one player holding off two armies is seen as 1 against 2.
	"""
	side = [p]
	for q in players:
		if q != p and players[q]['team_id'] == players[p]['team_id']:
			if max((army[q]['_fights'].get(m, {}).get(enemy, 0) for m in range(start_m, end_m + 3)), default=0) >= FIGHT_MIN_ORDERS:
				side.append(q)
	return side


def elo_scale(elos):
	"""
	Elo points that make a side 10x as strong, from how spread out this lobby's Elos are: in a lobby where Elos
	are close, a small gap means more. A gap of one standard deviation (at least 100) is a 62% favourite.
	"""
	elos = [e for e in elos if e is not None]
	if len(elos) < 2:
		return 400.0
	mean = sum(elos) / len(elos)
	sd = max(100.0, math.sqrt(sum((e - mean) ** 2 for e in elos) / len(elos)))
	return sd / -math.log10(1 / 0.62 - 1)


def side_expectation(elos_a, elos_b, scale):
	""" Expected share of the fight for side a: strength is the sum of 10^(elo/scale) of the players in it. """
	sa = sum(10 ** (e / scale) for e in elos_a)
	sb = sum(10 ** (e / scale) for e in elos_b)
	return sa / (sa + sb) if sa + sb else 0.5


def fight_records(players, army, A, end):
	"""
	Every major fight with what each side fielded and how it went against Elo: a, b (the pair), a_side / b_side
	(players on each side, allies included), a_lines / b_lines ({unit: count} queued in the 5 minutes before
	and during it), a_killed / b_killed, a_share (a's share of the value killed) and expected (a's expected share
	from Elo, None when an Elo is missing).
	"""
	scale = elo_scale([players[p].get('elo') for p in players])
	out = []
	for f in fight_episodes(players, army):
		a, b = f['a'], f['b']
		t0 = max(0, (f['start_m'] - FIGHT_ARMY_BEFORE) * 60000)
		t1 = min(end, (f['end_m'] + 3) * 60000)
		sa, sb = _side(players, army, a, b, f['start_m'], f['end_m']), _side(players, army, b, a, f['start_m'], f['end_m'])
		elos_a, elos_b = [players[p].get('elo') for p in sa], [players[p].get('elo') for p in sb]
		expected = side_expectation(elos_a, elos_b, scale) if None not in elos_a + elos_b else None
		total = f['a_killed'] + f['b_killed']
		out.append(dict(f, a_side=sa, b_side=sb, a_lines=dict(_army_between(A, a, t0, t1)),
			b_lines=dict(_army_between(A, b, t0, t1)), a_share=f['a_killed'] / total if total else 0.5, expected=expected,
			elo_scale=round(scale)))
	return out


def fight_vs_elo(players, fights):
	"""
	{player: (average of share of value killed minus the share their side's Elo predicts, army value fought)}:
	+0.10 means the player's fights went 10 points of share better than Elo expected. Bigger fights count more.
	"""
	acc = defaultdict(lambda: [0.0, 0.0])
	for f in fights:
		if f['expected'] is None:
			continue
		size = f['a_killed'] + f['b_killed']
		diff = f['a_share'] - f['expected']
		acc[f['a']][0] += size * diff
		acc[f['a']][1] += size
		acc[f['b']][0] -= size * diff
		acc[f['b']][1] += size
	return {p: (round(v[0] / v[1], 3), round(v[1])) for p, v in acc.items() if v[1]}


def key_moments(players, stats, castles, resigns, army=None, vest=None, A=(), end=0):
	"""
	A short list (at most 5, by size, shown in time order) of the events that shaped the game: major fights,
	big raids on villagers, castles destroyed, players eliminated (a regicide king killed or a defeat: their
	orders stop well before the end with no resignation) and the resignation that ends the analysis.
	All estimated from orders: the replay has no kill data.
	"""
	name = lambda p: players[p]['name']  # noqa: E731
	ev = []  # (size, time, text)

	# Major fights, per pair of players (so a sea fight and a land fight at the same time stay apart).
	if army:
		for f in fight_episodes(players, army):
			a, b, ka, kb, first, last_m = f['a'], f['b'], f['a_killed'], f['b_killed'], f['start_m'], f['end_m']
			win, lose, kw, kl = (a, b, ka, kb) if ka >= kb else (b, a, kb, ka)
			when = f" ({mmss(first * 60000)}–{mmss((last_m + 3) * 60000)})" if f['windows'] > 1 else ""
			late = first * 60000 >= end - LATE_WINDOW if end else False
			ev.append(((ka + kb) * (0.5 if late else 1), first * 60000,
				f"{name(win)} fought {name(lose)}: {round(kw):,} vs {round(kl):,} army value killed{when}"))

	# Raids: one player killing many of another player's villagers or fishing ships in one burst (kills under a
	# minute apart); bursts on the same player less than 5 minutes apart are one moment.
	if vest:
		for p in players:
			by_raider = defaultdict(list)
			for t, share, kind in vest[p].get('killed', []):
				by_raider[max(share, key=share.get)].append((t, kind))
			for raider, kills in by_raider.items():
				kills.sort()
				bursts, cur = [], []
				for k in kills:
					if cur and k[0] - cur[-1][0] > RAID_GAP:
						bursts.append(cur)
						cur = []
					cur.append(k)
				if cur:
					bursts.append(cur)
				raids = [b for b in bursts if len(b) >= RAID_MIN]
				for episode in _episodes(raids, lambda b: b[0][0]):
					kinds = Counter(kind for b in episode for _, kind in b)
					weight = sum(len(b) * RAID_VILLAGER_WEIGHT * (0.5 if end and b[0][0] >= end - LATE_WINDOW else 1)
						for b in episode)
					what = " and ".join(f"{kinds[k]} {k}{'s' if kinds[k] > 1 else ''}" for k in ('villager', 'fishing ship') if kinds[k])
					start, stop = episode[0][0][0], episode[-1][-1][0]
					when = f" ({mmss(start)}–{mmss(stop)})" if len(episode) > 1 else ""
					ev.append((weight, start, f"{name(raider)} raided {name(p)}: {what} killed{when}"))

	for c in castles:
		if c['status'] == 'lost':
			where = f" in {name(c['base'])}'s base" if c['base'] != c['player'] else ""
			ev.append((1500, c['t'], f"{name(c['player'])}'s castle{where} was destroyed"))

	# Eliminated (regicide king killed, or defeated): orders stop well before the end, no resignation.
	last = {}
	for a in A:
		if a.get('player_id') in players:
			last[a['player_id']] = a['t']
	for p, t in last.items():
		if p not in resigns and end and t < end - ELIMINATED_GAP:
			ev.append((10 ** 6, t, f"{name(p)} ({players[p]['civ']}) was eliminated (stopped playing, e.g. king killed)"))

	first_resign = min(resigns.items(), key=lambda kv: kv[1]) if resigns else None
	top = sorted(ev, key=lambda e: -e[0])[:MOMENTS_MAX - (1 if first_resign else 0)]
	out = [(t, text) for _, t, text in top]
	if first_resign:
		p, t = first_resign
		out.append((t, f"{name(p)} ({players[p]['civ']}) resigns"))
	out.sort(key=lambda e: e[0])
	return [(mmss(t), text) for t, text in out]