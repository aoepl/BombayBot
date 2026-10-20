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
}
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
		for a in mine:
			if a['a'] == 'DE_QUEUE':
				n = _name('objects', a['unit_id'])
				amt = a.get('amount', 1) or 1
				if n == 'Villager':
					vills += amt
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
			ages=ages, units=units, villagers=vills, first_military=first_military, builds=bcount,
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
	exec_parts = [
		_minmax(castle_click, invert=True),
		_minmax(imp_click, invert=True),
		_minmax({p: stats[p]['villagers'] for p in pids}),
		_minmax({p: stats[p]['first_military'] for p in pids}, invert=True),
		_minmax(peak_bank, invert=True),
	]
	# Pressure: the army a player keeps on the map (units plus castles and towers, valued by cost),
	# the army value sent into enemy bases, and forward castles and towers.
	army = army_presence(A, players, base_of, villagers, end, offensive)
	fwd_up = Counter(c['player'] for c in castles if c['forward'] and c['status'] in ('up', 'lost', 'deleted'))
	fwd_towers = Counter()
	for a in A:
		if a['a'] == 'BUILD' and a.get('player_id') in players and _name('objects', a['building_id']) in TOWERS:
			if players[base_of(a['x'], a['y'])]['team_id'] != players[a['player_id']]['team_id']:
				fwd_towers[a['player_id']] += 1
	on_map = _minmax({p: army[p]['avg_value_on_map'] for p in pids})
	sent = _minmax({p: army[p]['value_sent'] for p in pids})
	forward = _minmax({p: fwd_up[p] + 0.25 * fwd_towers[p] for p in pids})
	press = {p: 0.40 * on_map[p] + 0.45 * sent[p] + 0.15 * forward[p] for p in pids}
	for p in pids:
		stats[p]['army'] = army[p]
	# Pressure on each base = enemy army orders aimed into it, plus enemy castles and towers built in it.
	# A castle is only a big threat when an army comes with it: its weight grows with the enemy army
	# orders into that base from 3 minutes before it was placed to 5 minutes after.
	siege_in, cleared, siege_times = Counter(), Counter(), defaultdict(list)
	for c in castles:
		if not c['forward'] or c['status'] == 'unclear':  # only castles that were actually built
			continue
		m0 = int(c['t'] // 60000)
		army = sum(n for m, n in team_into[(players[c['player']]['team_id'], c['base'])].items() if m0 - 3 <= m <= m0 + 5)
		c['army_support'] = army
		c['threat'] = round(10 + min(40, army * 0.5))
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
	team_parts = [
		_minmax({p: trib_out[p] for p in pids}),
		_minmax({p: help_out[p] for p in pids}),
		_minmax({p: stats[p]['flares'] for p in pids}),
	]
	weights = {
		'Flank': dict(exec=.25, press=.35, surv=.25, team=.15),
		'Pocket': dict(exec=.35, press=.20, surv=.20, team=.25),
		None: dict(exec=.30, press=.30, surv=.20, team=.20),
	}
	for p in pids:
		e = sum(part[p] for part in exec_parts) / len(exec_parts)
		t = team_parts[0][p] * .5 + team_parts[1][p] * .35 + team_parts[2][p] * .15
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
		parts = strategy_parts(stats[p], [c for c in castles if c['player'] == p], A, p, players, base_of, attack_times[p])
		stats[p]['strategy'] = parts['name']
		stats[p]['strategy_parts'] = parts
		stats[p].update(
			press_out=press_out[p], press_in=press_in[p], pressure_in=pressure[p], help_out=help_out[p],
			trib_out=int(trib_out[p]), trib_in=int(trib_in[p]), peak_bank=peak_bank[p],
			top_target=max(((players[d]['civ'], n) for (a, d), n in press_matrix.items() if a == p), key=lambda x: x[1], default=None),
		)

	return dict(
		duration=end, players=players, stats=stats, castles=castles,
		tributes=[(a, b, int(v)) for (a, b), v in trib_pairs.most_common()],
		pressure=timeline_in, moments=key_moments(players, stats, castles, {p: t for p, t in resigns.items() if t <= end}),
	)


FEUDAL_NAMES = {
	'Archer': 'archers', 'Skirmisher': 'skirms', 'Scout Cavalry': 'scouts', 'Militia': 'Men-at-Arms',
	'Spearman': 'spears', 'Eagle Scout': 'Eagles', 'Camel Rider': 'camels', 'Cavalry Archer': 'cavalry archers',
	'Battle Elephant': 'elephants', 'Steppe Lancer': 'lancers',
}
NOT_ARMY = (MILITARY_SKIP - {'Monk'}) | {'Trade Cart', 'Trade Cog'}  # monk pushes are a strategy too


ENGAGE_RADIUS = 12  # tiles
ENGAGE_WINDOW = 60000  # ms


def find_engagements(A, players, base_of, villagers):
	""" ids (id(action)) of army orders that went at the enemy: their base, their units or their buildings. """
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

	offensive = set()
	for a in orders:
		team = players[a['player_id']]['team_id']
		target = a.get('target_id') or 0
		if target > 0 and owner.get(target) in players and players[owner[target]]['team_id'] != team:
			offensive.add(id(a))
			continue
		if players[base_of(a['x'], a['y'])]['team_id'] != team:
			offensive.add(id(a))
			continue
		m = int(a['t'] // 60000)
		for other in by_minute[m - 1] + by_minute[m] + by_minute[m + 1]:
			if players[other['player_id']]['team_id'] != team and abs(other['t'] - a['t']) <= ENGAGE_WINDOW \
					and math.dist((other['x'], other['y']), (a['x'], a['y'])) <= ENGAGE_RADIUS:
				offensive.add(id(a))
				break
	return offensive


def army_presence(A, players, base_of, villagers, end, offensive):
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
				queued[a['player_id']].append((a['t'], unit_value(a['unit_id']), a.get('amount', 1) or 1))

	out = {}
	for pid in pids:
		alive = [0] * minutes
		for i in military[pid]:
			first, last = seen[(pid, i)]
			for m in range(int(first // 60000), min(minutes - 1, int(min(end, last + ARMY_ALIVE_GRACE) // 60000)) + 1):
				alive[m] += 1
		alive = [min(n, POP_CAP) for n in alive]  # an estimate can never beat the population cap
		value_per_unit, total, count, q = [0.0] * minutes, 0.0, 0, sorted(queued[pid])
		j = 0
		for m in range(minutes):
			while j < len(q) and q[j][0] <= (m + 1) * 60000:
				total += q[j][1] * q[j][2]
				count += q[j][2]
				j += 1
			value_per_unit[m] = total / count if count else 0.0
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
		window = range(min(15, minutes - 1), minutes)  # from 15 minutes on, when armies matter
		on_map = [alive[m] * value_per_unit[m] + structures[m] for m in window]
		out[pid] = dict(
			avg_units=round(sum(alive[m] for m in window) / len(window)),
			peak_units=max(alive) if alive else 0,
			avg_value_on_map=round(sum(on_map) / len(on_map)),
			unit_value=round(value_per_unit[-1]),
			value_sent=round(sum(value_per_unit[m] for _, m in sent)),
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
	return [names.get(n, n) for n, c in counter.most_common() if c >= minimum][:limit]


def _army_lines(counter, minimum=5):
	""" Top two unit lines, plus any siege the player made (2 or more) that isn't already listed. """
	lines = _top_lines(counter, minimum)
	for n, c in counter.most_common():
		label = LINE_NAMES.get(n, n)
		if n in SIEGE and c >= 2 and label not in lines:
			lines.append(label)
	return lines


def strategy_parts(st, my_castles, A, pid, players, base_of, attack_times):
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

	if forward_towers >= 2:
		opening = "Feudal tower rush"
	elif sum(feudal_army.values()) >= 8:
		opening = "Feudal " + " + ".join(feudal_units or ["army"]) + ("" if feudal_attack else " (defensive)")
	elif feudal and castle and castle - feudal <= 4 * 60000:
		opening = "Fast Castle"
	else:
		opening = "Standard"
	boom = bool(castle and st.get('tc_after_castle', 0) >= 2)

	# Castle Age and Imperial Age armies, separately.
	castle_units = _army_lines(_army_between(A, pid, castle, imp or end)) if castle else []
	imperial_units = _army_lines(_army_between(A, pid, imp, end)) if imp else []
	main_units = castle_units or imperial_units
	fast_imp = bool(imp and imp < 25 * 60000)
	forward_castles = sum(1 for c in my_castles if c['forward'])
	tower_creep = st['towers'] >= 8

	parts = []
	if opening != "Standard" or boom:
		parts.append((opening if opening != "Standard" else "Castle Age") + (" boom" if boom else ""))
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
		opening=opening, boom=boom, feudal_units=feudal_units, feudal_attack=feudal_attack,
		forward_towers=forward_towers, castle_units=castle_units, imperial_units=imperial_units,
		main_units=main_units, fast_imp=fast_imp, forward_castles=forward_castles, tower_creep=tower_creep,
	)


def key_moments(players, stats, castles, resigns):
	ev = []
	clicks = [(stats[p]['ages'].get('Castle Age'), p) for p in players if stats[p]['ages'].get('Castle Age')]
	if clicks:
		t, p = min(clicks)
		ev.append((t, f"{players[p]['name']} ({players[p]['civ']}) is first to click Castle Age"))
	imps = [(stats[p]['ages'].get('Imperial Age'), p) for p in players if stats[p]['ages'].get('Imperial Age')]
	if imps:
		t, p = min(imps)
		ev.append((t, f"{players[p]['name']} ({players[p]['civ']}) is first to click Imperial Age"))
	first_mil = [(stats[p]['first_military'], p) for p in players if stats[p]['first_military']]
	if first_mil:
		t, p = min(first_mil)
		ev.append((t, f"First military unit queued by {players[p]['name']} ({players[p]['civ']})"))
	for c in castles:
		if c['forward']:
			outcome = {'up': 'went up', 'lost': 'went up and was likely destroyed', 'deleted': 'was deleted',
				'unclear': 'outcome unclear'}[c['status']]
			ev.append((c['t'], f"{players[c['player']]['civ']} castle in the {players[c['base']]['civ']} base ({outcome})"))
	for p, t in resigns.items():
		ev.append((t, f"{players[p]['name']} ({players[p]['civ']}) resigns"))
	ev.sort(key=lambda e: e[0])
	return [(mmss(t), txt) for t, txt in ev]
