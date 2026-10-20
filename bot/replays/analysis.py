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
				if n in AGES and n not in ages:
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
		status, oid = 'unclear', None
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
		trained = Counter(_name('objects', a['unit_id']) for a in produced if a['a'] == 'DE_QUEUE')
		castles.append(dict(
			t=b['t'], player=pid, kind=_name('objects', b['building_id']), base=where,
			forward=where != pid and players[where]['team_id'] != players[pid]['team_id'],
			status=status, deleted_at=deleted.get(oid), trained=trained.most_common(2), oid=oid
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
	army = army_presence(A, players, base_of, villagers, end, offensive, imp_at, water)
	fwd_up = Counter(c['player'] for c in castles if c['forward'] and c['status'] in ('up', 'lost', 'deleted'))
	fwd_towers = Counter()
	for a in A:
		if a['a'] == 'BUILD' and a.get('player_id') in players and _name('objects', a['building_id']) in TOWERS:
			if players[base_of(a['x'], a['y'])]['team_id'] != players[a['player_id']]['team_id']:
				fwd_towers[a['player_id']] += 1
	sent = _minmax({p: army[p]['value_sent'] for p in pids})
	forward_raw = {p: fwd_up[p] + 0.25 * fwd_towers[p] for p in pids}
	forward = _minmax(forward_raw)
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
		stats[p]['villagers_lost'] = len(v['lost'])
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
	playstyle(A, players, stats, army, floating, end, trib_out)
	# Pressure: army on the map 35%, army value sent at the enemy 40%, damage done (enemy army value killed in
	# fights plus enemy villagers killed) 15%, forward castles and towers 10%.
	dealt = {p: army[p]['value_killed_in_fights'] + stats[p]['_vill_kill_value'] for p in pids}
	damage = _minmax(dealt)
	# Army on the map only matters if it does damage: scale it by damage done per army value, with full
	# credit at the lobby median or better and nothing for an army that never hurt anyone.
	per_army = {p: dealt[p] / army[p]['avg_value_on_map'] for p in pids if army[p]['avg_value_on_map']}
	median = sorted(per_army.values())[len(per_army) // 2] if per_army else 0
	for p in pids:
		eff = min(1.0, per_army.get(p, 0) / median) if median else 1.0
		army[p]['damage_efficiency'] = round(eff, 2)
	on_map = _minmax({p: army[p]['avg_value_on_map'] * army[p]['damage_efficiency'] for p in pids})
	# Parts nobody did (no forward castles or towers in the game) are left out and their weight spread over the rest.
	press = {p: _combine([(0.35, None, on_map), (0.40, None, sent), (0.15, None, damage), (0.10, forward_raw, forward)], p)
		for p in pids}
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
	survival, survival_note = {}, {}
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
		difficulty = min(1.0, pressure[p] / (0.6 * max_in))
		value = 1 + held * (4 + 5 * difficulty) + min(cleared[p], 1.0)
		survival[p] = max(1.0, min(10.0, value))
		n_cleared = stats[p]['enemy_castles_cleared']
		cleared_txt = f", cleared {n_cleared} enemy castle{'s' if n_cleared != 1 else ''}" if n_cleared else ""
		level = 'heavy' if difficulty >= 0.66 else 'moderate' if difficulty >= 0.33 else 'light'
		stats[p]['pressure_level'] = level
		survival_note[p] = (
			f"held the base under {level} pressure ({round(home * 100)}% of new economy buildings at home{cleared_txt})" if held >= 0.75 else
			f"base partly lost ({round(home * 100)}% of new economy buildings at home, {moved_tcs} Town Centers rebuilt elsewhere{cleared_txt})"
			if held >= 0.4 else
			f"main base lost ({round(home * 100)}% of new economy buildings at home, {moved_tcs} Town Centers rebuilt elsewhere{cleared_txt})"
		)
	team_raw = [{p: trib_out[p] for p in pids}, {p: help_out[p] for p in pids}, {p: stats[p]['flares'] for p in pids}]
	team_parts = [_minmax(raw) for raw in team_raw]  # parts nobody did (no tributes in the game) are left out
	weights = {
		'Flank': dict(exec=.27, press=.36, surv=.27, team=.10),
		'Pocket': dict(exec=.40, press=.25, surv=.25, team=.10),
		None: dict(exec=.32, press=.32, surv=.26, team=.10),
	}
	for p in pids:
		e = _combine(list(zip(EXEC_WEIGHTS, exec_raw, exec_parts)), p)
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

	return dict(
		duration=end, players=players, stats=stats, castles=castles, water_weight=water,
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
VILLAGER_KILL_RADIUS = 10  # tiles
VILLAGER_KILL_ORDERS = 3   # enemy army orders near a villager's last position that mean it was likely killed
START_VILLAGERS = {'Chinese': 6, 'Mayans': 4}


def villager_estimate(A, players, villagers, army_alive, end):
	"""
	Estimated economy workers alive per minute: villagers plus fishing ships. The replay has no unit counts, so:
	- made: each queue click is 1 villager (5 with Shift); with several Town Centers selected each villager
	  goes to the TC with the shortest queue, and a TC trains one every 25 s, so over-queueing is not counted;
	- lost: a villager whose last order was followed, within 3 minutes, by 3+ enemy army orders within
	  10 tiles of it is counted as killed; deleted villagers are subtracted too;
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
	enemy_orders = defaultdict(list)
	for a in A:
		ids = a.get('object_ids') or []
		if a['a'] in ARMY_ORDERS and a.get('player_id') in players and ids and a.get('x') and a['x'] > 0 \
				and not all(i in vill for i in ids):
			enemy_orders[int(a['t'] // 60000)].append(a)
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
		killers = Counter()  # enemy player -> villagers of this player they killed (shared by orders nearby)
		killed = []  # (time, {enemy: share}) for each villager counted as killed
		for o, (t, x, y) in last.items():
			if owner.get(o) != pid or o in deleted or t > end - 4 * 60000:
				continue
			m, near = int(t // 60000), Counter()
			for k in range(m - 1, m + 4):
				for e in enemy_orders[k]:
					if players[e['player_id']]['team_id'] != p['team_id'] and t - 60000 <= e['t'] <= t + 180000 \
							and math.dist((x, y), (e['x'], e['y'])) <= VILLAGER_KILL_RADIUS:
						near[e['player_id']] += 1
			if sum(near.values()) >= VILLAGER_KILL_ORDERS:
				lost.append(t)
				total = sum(near.values())
				for e, n in near.items():
					killers[e] += n / total
				killed.append((t, {e: n / total for e, n in near.items()}))
		lost.sort()
		start = START_VILLAGERS.get(p.get('civ'), 3)
		alive = []
		for m in range(minutes):
			t = m * 60000
			n = start + sum(1 for x in made if x <= t) - sum(1 for x in lost if x <= t)
			army = army_alive[pid][min(len(army_alive[pid]) - 1, m)] if army_alive.get(pid) else 0
			alive.append(max(0, min(n, POP_CAP - army)))
		out[pid] = dict(made=made, lost=lost, alive=alive, villagers=kinds['Villager'], fishing=kinds['Fishing Ship'],
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


def army_presence(A, players, base_of, villagers, end, offensive, imp_at=None, water=1.0):
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
	forward_castles = sum(1 for c in my_castles if c['forward'])
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


MOMENTS_MAX = 5
BATTLE_MIN = 1500     # army value lost (both sides) in a 3-minute window that makes a major fight
RAID_MIN = 6          # villagers of one player killed within 3 minutes that make a big raid
ELIMINATED_GAP = 3 * 60000  # stopped giving orders this long before the end without resigning


def key_moments(players, stats, castles, resigns, army=None, vest=None, A=(), end=0):
	"""
	A short list (at most 5, by size, shown in time order) of the events that shaped the game: major fights,
	big raids on villagers, castles destroyed, players eliminated (a regicide king killed or a defeat: their
	orders stop well before the end with no resignation) and the resignation that ends the analysis.
	All estimated from orders: the replay has no kill data.
	"""
	name = lambda p: players[p]['name']  # noqa: E731
	team_of = {p: players[p]['team_id'] for p in players}
	ev = []  # (size, time, text)

	# Major fights: army value lost by each side over 3 minutes, counting only units lost while fighting.
	if army:
		lost = defaultdict(Counter)  # minute -> player -> value lost
		for p in players:
			for t_last, value in army[p]['_gone']:
				m = int(t_last // 60000)
				if max(army[p]['_fight_orders'].get(k, 0) for k in (m - 1, m, m + 1, m + 2)) >= FIGHT_MIN_ORDERS:
					lost[m][p] += value
		windows = []
		for m in sorted(lost):
			total = Counter()
			for k in (m, m + 1, m + 2):
				total.update(lost.get(k, {}))
			windows.append((sum(total.values()), m, total))
		used = set()
		for size, m, total in sorted(windows, key=lambda w: -w[0]):
			if size < BATTLE_MIN or used & {m, m + 1, m + 2}:
				continue
			used.update({m - 2, m - 1, m, m + 1, m + 2})
			sides = defaultdict(float)
			for p, v in total.items():
				sides[team_of[p]] += v
			if len(sides) < 2:
				continue
			# Name the players who did the most fighting on each side in this window.
			fought = Counter()
			for p in players:
				for k in (m, m + 1, m + 2):
					fought[p] += sum(army[p]['_fights'].get(k, {}).values())
			def who(tm):
				ps = sorted((p for p in players if team_of[p] == tm and fought[p] >= 1), key=lambda p: -fought[p])[:2]
				return " & ".join(name(p) for p in ps) or "their team"
			(t1, v1), (t2, v2) = sorted(sides.items(), key=lambda kv: kv[1])[:2]  # t1 lost less: won the fight
			text = f"Major fight: {who(t1)} beat {who(t2)} (lost {round(v1):,} vs {round(v2):,} army value)"
			ev.append((size, m * 60000, text))

	# Big raids: many villagers of one player killed in a few minutes.
	if vest:
		for p in players:
			kills = vest[p].get('killed', [])
			i = 0
			while i < len(kills):
				j = i
				while j + 1 < len(kills) and kills[j + 1][0] - kills[i][0] <= 3 * 60000:
					j += 1
				if j - i + 1 >= RAID_MIN:
					credit = Counter()
					for _, share in kills[i:j + 1]:
						credit.update(share)
					raider = credit.most_common(1)[0][0]
					ev.append(((j - i + 1) * 100 * 1.5, kills[i][0],
						f"{name(raider)} raided {name(p)}: about {j - i + 1} villagers killed"))
					i = j + 1
				else:
					i += 1

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