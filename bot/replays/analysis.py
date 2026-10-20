# -*- coding: utf-8 -*-
"""
Turn a parsed recorded game into per-player performance data.

Everything here is derived from player commands and the game's periodic sync
snapshots, so numbers are proxies: age times are when the age-up was clicked,
unit counts are queue clicks (cancelled units included) and each unit line is
named after its base unit (Archer covers Crossbowmen and Arbalesters, etc.).
"""
import math
from collections import Counter, defaultdict

from .rec_parser import reference

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
	players = {p['number']: dict(p) for p in rec['players']}
	for p in players.values():
		p['civ'] = civ_name(p['civ_id'])
		m = meta.get(p['profile_id'], {})
		p['rating'] = m.get('rating')
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
	resigns = {a['player_id']: a['t'] for a in A if a['a'] == 'RESIGN' and a.get('player_id') in players}
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

	# ---- army orders: pressure dealt, pressure received, help given to allies
	press_out, press_in, help_out = Counter(), Counter(), Counter()
	press_matrix = Counter()
	timeline_in = defaultdict(Counter)  # defender -> minute -> orders
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
		if b == pid:
			continue
		if players[b]['team_id'] != players[pid]['team_id']:
			press_out[pid] += 1
			press_in[b] += 1
			press_matrix[(pid, b)] += 1
			timeline_in[b][int(a['t'] // 60000)] += 1
		else:
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
	fwd_up = Counter(c['player'] for c in castles if c['forward'] and c['status'] in ('up',))
	press = _minmax({p: press_out[p] + 25 * fwd_up[p] for p in pids})
	kept = {}
	for p in pids:
		c = curve.get(p)
		if not c:
			kept[p] = None
			continue
		peak = max(r[1] for r in c)
		kept[p] = (obj_at(p, 0.95) or 0) / peak if peak else None
	surv_raw = _minmax(kept)
	under = _minmax({p: press_in[p] for p in pids})
	survival = {}
	for p in pids:
		v = surv_raw[p] * 0.7 + under[p] * 0.3 - 3 * min(stats[p]['tc_moves'], 1) + (1 if players[p]['won'] else 0)
		survival[p] = max(1.0, min(10.0, v))
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
		sc = dict(exec=round(e, 1), press=round(press[p], 1), surv=round(survival[p], 1), team=round(t, 1))
		w = weights[players[p]['seat']]
		sc['overall'] = round(sum(sc[k] * w[k] for k in w), 1)
		stats[p]['scores'] = sc

	# ---- strategy names
	for p in pids:
		stats[p]['strategy'] = strategy_name(stats[p], [c for c in castles if c['player'] == p], A, p)
		stats[p].update(
			press_out=press_out[p], press_in=press_in[p], help_out=help_out[p],
			trib_out=int(trib_out[p]), trib_in=int(trib_in[p]), peak_bank=peak_bank[p],
			top_target=max(((players[d]['civ'], n) for (a, d), n in press_matrix.items() if a == p), key=lambda x: x[1], default=None),
		)

	return dict(
		duration=end, players=players, stats=stats, castles=castles,
		tributes=[(a, b, int(v)) for (a, b), v in trib_pairs.most_common()],
		pressure=timeline_in, moments=key_moments(players, stats, castles, resigns),
	)


def strategy_name(st, my_castles, A, pid):
	ages = st['ages']
	feudal, castle, imp = (ages.get(a) for a in AGES)
	parts = []

	towers_early = 0
	if castle:
		towers_early = sum(1 for a in A if a['a'] == 'BUILD' and a.get('player_id') == pid
			and _name('objects', a['building_id']) in TOWERS and a['t'] < castle)
	feudal_army = 0
	if feudal and castle:
		feudal_army = sum((a.get('amount', 1) or 1) for a in A if a['a'] == 'DE_QUEUE' and a.get('player_id') == pid
			and feudal <= a['t'] < castle and _name('objects', a['unit_id']) not in MILITARY_SKIP)
	if towers_early >= 2:
		parts.append("Feudal tower rush")
	elif feudal_army >= 8:
		parts.append("Feudal aggression")
	elif feudal and castle and castle - feudal <= 4 * 60000:
		parts.append("Fast Castle")
	if castle and st.get('tc_after_castle', 0) >= 2:
		parts[-1:] = [(parts[-1] + " boom") if parts else "Castle Age boom"]

	army = [(n, c) for n, c in st['units'].most_common() if n not in ('Villager', 'Trade Cart', 'Trade Cog')]
	main = [LINE_NAMES.get(n, n) for n, c in army[:2] if c >= 5]
	if main:
		parts.append(" + ".join(main))
	extras = []
	if imp and imp < 25 * 60000:
		extras.append("fast Imperial")
	fwd = [c for c in my_castles if c['forward']]
	if fwd:
		extras.append(f"{len(fwd)} forward castle{'s' if len(fwd) > 1 else ''}")
	if st['towers'] >= 8:
		extras.append("tower creep")
	if extras:
		parts.append(", ".join(extras))
	return " → ".join(parts) if parts else "Standard play"


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
