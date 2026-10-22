# -*- coding: utf-8 -*-
"""
Reports over the saved games (insights.py): a player's stats (/aoe stat) and civ statistics (/aoe civs).
Only games analysed in the asking queue channel are used. Everything here is pure functions over rows, so it
can be tested without Discord; load_rows() is the only database call.
"""
from collections import Counter, defaultdict

from core.database import db

MIN_GAMES = 2        # games in a seat/style/strategy before it can be someone's "best"
MIN_CIV_GAMES = 2    # games with a civ before it can be listed among the best civs
ELO_RANGES = ((0, 1400, "under 1400"), (1400, 1600, "1400–1599"), (1600, 1800, "1600–1799"), (1800, 99999, "1800+"))

# Army style of a game, from the unit lines a player made (castle and imperial armies, then Feudal units).
STYLE_WORDS = (
	('Monks', ('Monk',)),
	('Naval', ('Galley', 'Hulk', 'Fire ship', 'Demo ship', 'Galleon', 'Carrack', 'Longboat', 'Caravel', 'Dromon',
		'Turtle', 'Thirisadai')),
	('Gunpowder', ('Hand Cannon', 'Janissar', 'Conquistador')),
	('Cavalry archers', ('Cavalry Archer', 'Mangudai', 'Kipchak', 'Keshik', 'Camel Archer', 'Elephant Archer')),
	('Archers', ('Archer', 'Skirmisher', 'Longbow', 'Chu Ko Nu', 'Plumed', 'Genoese', 'Rattan', 'Composite', 'archers',
		'skirms')),
	('Cavalry', ('Knight', 'Scout', 'Hussar', 'Camel', 'Elephant', 'Lancer', 'Cataphract', 'Boyar', 'Konnik', 'Leitis',
		'Coustillier', 'Monaspa', 'scouts', 'camels', 'elephants', 'lancers')),
	('Infantry', ('Militia', 'Spear', 'Halb', 'Eagle', 'Axeman', 'Throwing', 'Huskarl', 'Samurai', 'Teutonic', 'Berserk',
		'Woad', 'Jaguar', 'Shotel', 'Gbeto', 'Karambit', 'Urumi', 'Serjeant', 'Temple Guard', 'Men-at-Arms', 'spears')),
)


SIEGE_WORDS = ('Ram', 'Mangonel', 'Onager', 'Scorpion', 'Trebuchet', 'Bombard', 'Siege', 'Organ', 'Rocket')


def army_style(row):
	""" The style of the player's main unit line (Castle Age army first), siege left out. """
	lines = []
	for field in ('castle_units', 'imperial_units', 'main_units', 'feudal_units'):
		for line in (row.get(field) or '').replace(' + ', ', ').split(', '):
			if line.strip() and not any(w in line for w in SIEGE_WORDS):
				lines.append(line.strip())
	for line in lines:
		for style, words in STYLE_WORDS:
			if any(w.lower() in line.lower() for w in words):
				return style
	return 'Other'


def strategy_key(row):
	""" The opening plus the boom, e.g. "Fast Castle, farm boom" (comparable across games). """
	parts = [row.get('opening') or 'Standard']
	if row.get('feudal_boom') and 'boom' not in parts[0]:
		parts[0] = 'Feudal boom'
	boom = ('farm + fish boom' if row.get('boom') and row.get('fish_boom') else 'fish boom' if row.get('fish_boom')
		else 'farm boom' if row.get('boom') else '')
	if boom and boom not in parts[0]:  # "Feudal fish boom" already says it
		parts.append(boom)
	return ", ".join(parts)


async def load_rows(channel_id):
	""" Player rows of every game analysed in this queue channel. """
	games = {g['game_key']: g for g in await db.select(('game_key', 'channel_id', 'map_name', 'map_category'), 'aoe_games',
		where=dict(channel_id=channel_id))}
	cols = ('game_key', 'user_id', 'name', 'civ', 'team', 'seat', 'won', 'elo', 'apm', 'opening', 'boom', 'fish_boom',
		'feudal_boom', 'main_units', 'feudal_units', 'castle_units', 'imperial_units', 'score_overall', 'elo_delta',
		'map_name', 'map_category')
	return [r for r in await db.select(cols, 'aoe_game_players') if r['game_key'] in games]


def _rate(wins, games):
	return f"{round(100 * wins / games)}%" if games else "—"


def _best(rows, key):
	""" The value of key(row) with the highest average score (at least MIN_GAMES games), or None. """
	groups = defaultdict(list)
	for r in rows:
		if r.get('score_overall') is not None:
			groups[key(r)].append(r['score_overall'])
	ok = {k: sum(v) / len(v) for k, v in groups.items() if len(v) >= MIN_GAMES and k}
	return max(ok, key=ok.get) if ok else None


def bests(rows):
	""" A player's best seat, army style and strategy, by average overall score. """
	return dict(seat=_best(rows, lambda r: r.get('seat')), style=_best(rows, army_style), strategy=_best(rows, strategy_key))


def _got_best(row, best):
	""" Share (0..1) of the player's known bests they got in this game, or None when no best is known yet. """
	checks = [(best['seat'], row.get('seat')), (best['style'], army_style(row)), (best['strategy'], strategy_key(row))]
	checks = [(b, v) for b, v in checks if b]
	return sum(1 for b, v in checks if b == v) / len(checks) if checks else None


# ---------- /aoe stat

def player_summary(all_rows, user_id):
	mine = [r for r in all_rows if r.get('user_id') == user_id]
	if not mine:
		return None
	decided = [r for r in mine if r.get('won') is not None]
	seats = {}
	for seat in ('Flank', 'Pocket'):
		rs = [r for r in mine if r.get('seat') == seat]
		w = [r for r in rs if r.get('won') is not None]
		seats[seat] = (len(rs), _rate(sum(1 for r in w if r['won']), len(w)))
	apms = [r['apm'] for r in mine if r.get('apm')]
	deltas = [r['elo_delta'] for r in mine if r.get('elo_delta') is not None]
	best = bests(mine)

	# Individual luck: how often the player got their best seat, army style and strategy.
	got = [g for g in (_got_best(r, best) for r in mine) if g is not None]
	# Team luck: how often their teammates got their own bests (teammates with enough games to have bests).
	by_user = defaultdict(list)
	for r in all_rows:
		if r.get('user_id'):
			by_user[r['user_id']].append(r)
	best_of = {u: bests(rs) for u, rs in by_user.items() if u != user_id}
	team_got = []
	for r in mine:
		for t in all_rows:
			if t['game_key'] == r['game_key'] and t.get('team') == r.get('team') and t.get('user_id') \
					and t['user_id'] != user_id and t['user_id'] in best_of:
				g = _got_best(t, best_of[t['user_id']])
				if g is not None:
					team_got.append(g)
	civs = Counter(r['civ'] for r in mine if r.get('civ'))
	return dict(
		games=len(mine), wins=sum(1 for r in decided if r['won']), decided=len(decided), seats=seats,
		apm=round(sum(apms) / len(apms)) if apms else None,
		avg_delta=round(sum(deltas) / len(deltas), 1) if deltas else None,
		above=sum(1 for d in deltas if d >= 1.5), below=sum(1 for d in deltas if d <= -1.5), rated=len(deltas),
		best=best, luck=round(100 * sum(got) / len(got)) if got else None,
		team_luck=round(100 * sum(team_got) / len(team_got)) if team_got else None,
		top_civs=civs.most_common(3), name=mine[-1].get('name'),
	)


def strats_lines(s):
	""" The /aoe stat reply as (field name, value) pairs. """
	verdict = "—"
	if s['avg_delta'] is not None:
		d = s['avg_delta']
		verdict = ("well above their Elo" if d >= 3 else "above their Elo" if d >= 1.5 else
			"well below their Elo" if d <= -3 else "below their Elo" if d <= -1.5 else "around their Elo")
		verdict += f" (average {d:+}; above in {s['above']}, below in {s['below']} of {s['rated']} games)"
	b = s['best']
	lines = [
		("Games", f"{s['games']} analysed · won {s['wins']} of {s['decided']} ({_rate(s['wins'], s['decided'])})"),
		("Positions", f"Flank {s['seats']['Flank'][0]} games (won {s['seats']['Flank'][1]}) · "
			f"Pocket {s['seats']['Pocket'][0]} games (won {s['seats']['Pocket'][1]})"),
		("Average APM", str(s['apm'] or "—")),
		("Against their BombayBot Elo", verdict),
		("Best", f"Position: {b['seat'] or '—'} · Army style: {b['style'] or '—'} · Strategy: {b['strategy'] or '—'}"
			f"\n(by average score, {MIN_GAMES}+ games each)"),
		("Individual luck", f"{s['luck']}% of their bests (position, army style, strategy) per game"
			if s['luck'] is not None else "Not enough games yet"),
		("Team luck", f"{s['team_luck']}% of their teammates' bests per game"
			if s['team_luck'] is not None else "Not enough games yet"),
		("Most played civs", ", ".join(f"{c} ×{n}" for c, n in s['top_civs']) or "—"),
	]
	return lines


# ---------- /aoe civs

def civ_summary(rows, map_filter=None):
	if map_filter:
		f = map_filter.lower().replace(" ", "")
		rows = [r for r in rows if f in (r.get('map_name') or '').lower().replace(" ", "")
			or f in (r.get('map_category') or '').lower().replace(" ", "")]
	rows = [r for r in rows if r.get('won') is not None and r.get('civ')]

	def table(rs):
		g, w = Counter(), Counter()
		for r in rs:
			g[r['civ']] += 1
			w[r['civ']] += 1 if r['won'] else 0
		return {c: (w[c], g[c]) for c in g}

	def top(rs, n=3):
		t = table(rs)
		ok = [(c, wv, gv) for c, (wv, gv) in t.items() if gv >= MIN_CIV_GAMES]
		return sorted(ok, key=lambda x: (-x[1] / x[2], -x[2]))[:n]

	overall = sorted(table(rows).items(), key=lambda kv: (-kv[1][0] / kv[1][1], -kv[1][1]))
	by_elo = {label: top([r for r in rows if r.get('elo') is not None and lo <= r['elo'] < hi])
		for lo, hi, label in ELO_RANGES}
	by_seat = {seat: top([r for r in rows if r.get('seat') == seat]) for seat in ('Flank', 'Pocket')}
	maps = Counter(r.get('map_name') for r in rows if r.get('map_name'))
	by_map = {m: top([r for r in rows if r.get('map_name') == m]) for m, _ in maps.most_common(5)}
	return dict(games=len({r['game_key'] for r in rows}), overall=overall, by_elo=by_elo, by_seat=by_seat, by_map=by_map)


def civs_lines(c):
	fmt = lambda items: ", ".join(f"{civ} {_rate(w, g)} ({g})" for civ, w, g in items) or "not enough games"  # noqa: E731
	overall = [f"{civ}: {_rate(w, g)} of {g}" for civ, (w, g) in c['overall'][:15]]
	lines = [("Win rate by civ (games)", "\n".join(overall) or "No games yet")]
	lines.append(("Best by BombayBot Elo", "\n".join(f"{label}: {fmt(items)}" for label, items in c['by_elo'].items())))
	lines.append(("Best by position", "\n".join(f"{seat}: {fmt(items)}" for seat, items in c['by_seat'].items())))
	if c['by_map']:
		lines.append(("Best by map", "\n".join(f"{m}: {fmt(items)}" for m, items in c['by_map'].items())))
	return lines
