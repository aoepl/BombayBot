# -*- coding: utf-8 -*-
"""
Pictures of the map for a game's post, turned 45° like the in-game minimap, side by side:
- Start: trees, gold, stone, berries, hunt, sheep, fish and relics where the map generator put them, and each
  player's starting Town Center and walls (from the objects in the recorded game's header, rec_parser.map_objects).
- End: every building placed during the game (castles known to be lost or deleted left out; other destroyed
  buildings can't be told from the commands), walls, and each unit where it was last sent if it was given an order
  in the last 3 minutes, villagers small and army large.
"""
import io

from .rec_parser import reference

# Object types whose name in the reference table is missing or outdated, by what players did with them in recorded
# games (lumber camps built next to them; few animals clicked by both players).
EXTRA_KINDS = {1984: 'tree', 1053: 'tree', 302: 'tree', 2340: 'hunt', 2737: 'hunt'}

# Resource kind by words in the object's name (first match wins); stumps and dead plants aren't resources.
KIND_WORDS = (
	('none', ('Stump', 'Dead')),
	('gold', ('Gold Mine',)),
	('stone', ('Stone Mine',)),
	('berries', ('Forage Bush', 'Fruit Bush', 'Berry')),
	('tree', ('Tree', 'Bamboo', 'Palm', 'Forest')),
	('hunt', ('Wild Boar', 'Javelina', 'Deer', 'Ibex', 'Zebra', 'Ostrich', 'Rhinoceros', 'Elephant')),
	('livestock', ('Sheep', 'Turkey', 'Goat', 'Llama', 'Cow', 'Pig', 'Water Buffalo', 'Goose', 'Yak')),
	('fish', ('Fish', 'Marlin', 'Dorado', 'Salmon', 'Snapper', 'Tuna', 'Perch', 'Box Turtles')),
	('relic', ('Relic',)),
)

# How each kind is drawn: colour, marker, size (points²), alpha; listed bottom layer first.
STYLE = {
	'tree': ('#5f8a3c', 'o', 2.2, .55),
	'fish': ('#3f86c6', 'o', 9, .9),
	'berries': ('#c0457c', 'o', 9, .95),
	'stone': ('#8f8a80', 'o', 11, .95),
	'gold': ('#e0aa1e', 'o', 11, .95),
	'hunt': ('#8a5a2b', 'o', 14, .95),
	'livestock': ('#fbf8f0', 'o', 12, .95),
	'relic': ('#f3e7b3', 's', 18, 1),
}
LABELS = {'tree': 'Trees', 'gold': 'Gold', 'stone': 'Stone', 'berries': 'Berries', 'hunt': 'Hunt', 'livestock': 'Sheep',
	'fish': 'Fish', 'relic': 'Relics'}
# Player colours in the game's order (colour id 0 = blue, as in post.COLOR_EMOJI).
PLAYER_COLORS = ['#2f6ff0', '#e0362b', '#36a832', '#e8c51c', '#2fc0cf', '#a64bd1', '#a0a0a0', '#f08a1c']
WALL_WORDS = ('Wall', 'Gate')
TOWN_CENTER = 109


def kind_of(object_type):
	if object_type in EXTRA_KINDS:
		return EXTRA_KINDS[object_type]
	name = reference().get('objects', {}).get(str(object_type)) or ''
	for kind, words in KIND_WORDS:
		if any(w in name for w in words):
			return None if kind == 'none' else kind
	return None


END_RECENT = 180000   # units given an order in the game's last 3 minutes are drawn on the end map
SAME_SPOT = 1.5       # tiles: placements this close by the same player are one building (the last one)
BUILDING_SIZE = {'Town Center': 70, 'Castle': 60, 'Wonder': 70, 'Krepost': 45, 'Donjon': 40, 'Farm': 10, 'House': 9}
UNIT_COMMANDS = {'MOVE', 'ORDER', 'PATROL', 'DE_ATTACK_MOVE', 'ATTACK_GROUND', 'BUILD', 'WALL', 'REPAIR'}


def render(rec, result=None, dpi=120):
	"""
	-> PNG bytes: the starting map, and next to it the end of the game when result (analysis.analyze output) is
	given; None when the recording has no readable objects.
	rec: rec_parser.parse() output (players with number, name, color_id; map_objects; actions).
	"""
	objects = rec.get('map_objects') or []
	if not objects:
		return None
	from matplotlib.figure import Figure
	from matplotlib.backends.backend_agg import FigureCanvasAgg

	start = _start(rec)
	if start is None:
		return None
	panels = [('Start', start)]
	if result:
		end = _end(rec, result)
		end['towns'] = start['towns']  # names where each player started ...
		end['towns_lost'] = end.pop('lost_towns')  # ... without the Town Center when it was destroyed
		panels.append((f"End · {_clock(result['duration'])}", end))
	fig = Figure(figsize=(8.4 * len(panels) / 1.55 + .6, 5.4), dpi=dpi)
	FigureCanvasAgg(fig)
	fig.patch.set_facecolor('#2b2d31')  # Discord's dark background, so the corners blend in
	side = start['side']
	for k, (title, layers) in enumerate(panels):
		w = 1 / len(panels)
		ax = fig.add_axes([k * w + .005, 0.08, w - .01, 0.86])
		_draw(ax, rec, layers, side)
		ax.set_title(title, color='#dbdee1', fontsize=11, fontweight='bold', pad=2)
	shown = [k for k in STYLE if k in start['points']]
	handles = [fig.gca().scatter([], [], s=28 if k != 'tree' else 18, c=STYLE[k][0], marker=STYLE[k][1],
		edgecolors='#2a241b' if k in ('livestock', 'relic') else 'none') for k in shown]
	labels = [LABELS[k] for k in shown]
	if result:
		grey = '#c9c2b4'
		handles += [fig.gca().scatter([], [], s=26, c=grey, marker='s', edgecolors='#2a241b', linewidths=.6),
			fig.gca().scatter([], [], s=14, c=grey, marker='o', edgecolors='#2a241b', linewidths=.5),
			fig.gca().scatter([], [], s=4, c=grey, marker='o', linewidths=0)]
		handles.append(fig.gca().scatter([], [], s=24, c=STYLE['gold'][0], marker='o', alpha=.3, linewidths=0))
		handles.append(fig.gca().scatter([], [], s=40, c=grey, marker='X', edgecolors='#2a241b', linewidths=.6))
		labels += ['Buildings', 'Army (last 3 min)', 'Villagers', 'Mined from (end)', 'Town Center lost']
	fig.legend(handles, labels, loc='lower center', ncol=len(labels), frameon=False, fontsize=8.5,
		labelcolor='#dbdee1', handletextpad=.2, columnspacing=1.1)
	buf = io.BytesIO()
	fig.savefig(buf, format='png', facecolor=fig.get_facecolor())
	return buf.getvalue()


def _clock(ms):
	s = int(ms // 1000)
	return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _turn(pts):  # tile (x, y) -> minimap position: the square map turned 45°
	return [x - y for x, y in pts], [-(x + y) for x, y in pts]


def _start(rec):
	""" Layers of the starting map: resources, starting Town Centers and walls. """
	names = reference().get('objects', {})
	players = {p['number']: p for p in rec.get('players') or []}
	points, walls, towns = {}, {}, {}
	for owner, _, kind_id, x, y in rec.get('map_objects') or []:
		if owner == 0:
			kind = kind_of(kind_id)
			if kind:
				points.setdefault(kind, []).append((x, y))
		elif owner in players:
			name = names.get(str(kind_id)) or ''
			if kind_id == TOWN_CENTER:
				towns.setdefault(owner, (x, y))
			elif any(w in name for w in WALL_WORDS):
				walls.setdefault(owner, []).append((x, y))
	if not points and not towns:
		return None
	edge = max([max(x, y) for pts in points.values() for x, y in pts] + [max(x, y) for x, y in towns.values()] + [1])
	side = next((s for s in (120, 144, 168, 200, 220, 240, 252, 255, 480) if s >= edge), edge)
	return dict(points=points, wall_dots=walls, towns=towns, side=side)


def _end(rec, result):
	"""
	Layers of the end of the game, estimated from the commands (the recording has no map state):
	- buildings placed, without the ones destroyed or deleted (see _gone_buildings) and castles never seen built;
	- walls; units where they were last sent, if they got an order in the last 3 minutes;
	- resources: trees chopped (clicked, and the trees next to them) and hunt, sheep and relics taken are gone;
	  gold, stone and berry piles that were gathered from are drawn faded (how much is left can't be told).
	"""
	names = reference().get('objects', {})
	players = {p['number']: p for p in rec.get('players') or []}
	end = result['duration']
	actions = [a for a in rec.get('actions') or [] if a['t'] <= end]
	gone = [(c['player'], c['x'], c['y']) for c in result.get('castles') or [] if c.get('status') in ('lost', 'deleted', 'unclear')]
	buildings = []  # [player, x, y, name, t placed]
	lines = []      # (player, x0, y0, x1, y1, name)
	last = {}       # unit -> (t, player, x, y)
	for a in actions:
		p = a.get('player_id')
		if p not in players:
			continue
		x, y = a.get('x'), a.get('y')
		if a['a'] == 'BUILD' and x and x > 0:
			name = names.get(str(a.get('building_id'))) or ''
			buildings = [b for b in buildings if not (b[0] == p and abs(b[1] - x) < SAME_SPOT and abs(b[2] - y) < SAME_SPOT)]
			buildings.append((p, x, y, name, a['t']))
		elif a['a'] == 'WALL' and x and x > 0 and a.get('x_end') is not None:
			lines.append((p, x, y, a['x_end'], a['y_end'], names.get(str(a.get('building_id'))) or ''))
		if a['a'] in UNIT_COMMANDS and x and x > 0:
			for i in a.get('object_ids') or []:
				last[i] = (a['t'], p, x, y)
	destroyed = _gone_buildings(actions, players, buildings, end) | {
		k for k, b in enumerate(buildings) if any(g[0] == b[0] and abs(g[1] - b[1]) < 3 and abs(g[2] - b[2]) < 3 for g in gone)}
	buildings = [b[:4] for k, b in enumerate(buildings) if k not in destroyed]
	builders = {i for a in actions if a['a'] in ('BUILD', 'WALL', 'REPAIR') for i in a.get('object_ids') or []}
	units = [(p, x, y, i in builders) for i, (t, p, x, y) in last.items() if t >= end - END_RECENT]
	points, faded = _resources_left(rec, actions)
	lost = towns_lost(rec, actions, players, end)
	return dict(buildings=buildings, lines=lines, units=units, points=points, faded=faded, lost_towns=lost)


HIT_GONE = 60000   # enemies stopped hitting a building this long before the end ...
NEAR_SPOT = 2.5    # ... and it stood within this many tiles of the target's position: destroyed
CHOP = 2.5         # tiles around a clicked tree that were chopped too
PILE = 2.2         # tiles between tiles of one gold, stone or berry pile


def _gone_buildings(actions, players, buildings, end):
	"""
	Indexes of buildings destroyed or deleted. Attack orders carry the target's id and position: a target nobody
	ever gave a unit order to is a building (or wall); the building placed at that spot (by an enemy of the
	attacker, before the attack) is destroyed when the attacks on it stopped at least a minute before the end and
	its owner never used it again (training, research, gather point, own units sent into it). Deleted buildings
	are gone too, when their position is known from an order aimed at them.
	"""
	team = {p: v.get('team_id', p) for p, v in players.items()}
	units = {i for a in actions if a['a'] in UNIT_COMMANDS | {'STANCE', 'FORMATION', 'FOLLOW', 'GUARD'}
		for i in a.get('object_ids') or []}
	where, hits, used = {}, {}, {}
	for a in actions:
		p, tg = a.get('player_id'), a.get('target_id') or 0
		if a['a'] in ('DE_QUEUE', 'RESEARCH', 'GATHER_POINT', 'DE_MULTI_GATHERPOINT'):
			for i in a.get('object_ids') or []:
				used[i] = a['t']
		if a['a'] != 'ORDER' or tg <= 0 or tg in units or p not in players or not a.get('x'):
			continue
		where[tg] = (a['x'], a['y'])
		hits.setdefault(tg, []).append((a['t'], p))

	def building_at(x, y, attacker, t):
		best = None
		for k, (o, bx, by, _, t0) in enumerate(buildings):
			if t0 <= t and team.get(o) != team.get(attacker) and abs(bx - x) <= NEAR_SPOT and abs(by - y) <= NEAR_SPOT:
				if best is None or (bx - x) ** 2 + (by - y) ** 2 < (buildings[best][1] - x) ** 2 + (buildings[best][2] - y) ** 2:
					best = k
		return best

	out = set()
	for tg, hs in hits.items():
		enemy = [(t, p) for t, p in hs if building_at(*where[tg], p, t) is not None]
		if not enemy:
			continue
		t_last, attacker = enemy[-1]
		k = building_at(*where[tg], attacker, t_last)
		if _lost(hs, used.get(tg), buildings[k][0], team, end):
			out.add(k)
	for a in actions:
		if a['a'] == 'DELETE':
			for i in a.get('object_ids') or []:
				if i in where and i not in units:
					k = building_at(*where[i], -1, a['t'])
					if k is not None and buildings[k][0] == a.get('player_id'):
						out.add(k)
	return out


USED_AFTER = 180000   # an attacked building its owner kept using for this long after the last hit still stood
QUIET_END = 300000    # ... or one still used in the game's last 5 minutes


def _lost(hits, last_used, owner, team, end):
	"""
	Was an attacked building destroyed? hits: [(t, player)] orders aimed at it. Yes when enemies stopped hitting it a
	minute or more before the end, the owner's team didn't aim at it after that (repairs), and the owner stopped
	using it (training, research, gather point) within 3 minutes of the last hit and not in the last 5 minutes.
	"""
	enemy = [t for t, p in hits if team.get(p) != team.get(owner)]
	if not enemy:
		return False
	t_last = enemy[-1]
	if end - t_last < HIT_GONE or any(t > t_last and team.get(p) == team.get(owner) for t, p in hits):
		return False
	return last_used is None or (last_used <= t_last + USED_AFTER and end - last_used >= QUIET_END)


def towns_lost(rec, actions, players, end):
	""" Players whose starting Town Center (from the header) was destroyed, by the same rule as other buildings. """
	team = {p: v.get('team_id', p) for p, v in players.items()}
	tcs = {inst: owner for owner, inst, t, x, y in rec.get('map_objects') or [] if t == TOWN_CENTER and owner in players}
	hits, used = {i: [] for i in tcs}, {}
	for a in actions:
		if a['a'] == 'ORDER' and a.get('target_id') in tcs and a.get('player_id') in players:
			hits[a['target_id']].append((a['t'], a['player_id']))
		if a['a'] in ('DE_QUEUE', 'RESEARCH', 'GATHER_POINT', 'DE_MULTI_GATHERPOINT'):
			for i in a.get('object_ids') or []:
				if i in tcs:
					used[i] = a['t']
	return {owner for i, owner in tcs.items() if _lost(hits[i], used.get(i), owner, team, end)}


def _resources_left(rec, actions):
	""" -> ({kind: [(x, y)]} still there, {kind: [(x, y)]} gathered from) at the end, estimated from the clicks. """
	targeted, taken = set(), set()
	for a in actions:
		if a.get('target_id'):
			targeted.add(a['target_id'])
		taken.update(a.get('object_ids') or [])  # sheep are commanded by whoever has them
	gaia = [(inst, kind_of(t), x, y) for owner, inst, t, x, y in rec.get('map_objects') or [] if owner == 0]
	gaia = [g for g in gaia if g[1]]
	chopped = [(x, y) for inst, kind, x, y in gaia if kind == 'tree' and inst in targeted]
	mined = [(kind, x, y) for inst, kind, x, y in gaia if kind in ('gold', 'stone', 'berries') and inst in targeted]
	left, faded = {}, {}
	for inst, kind, x, y in gaia:
		if kind in ('hunt', 'livestock', 'relic') and (inst in targeted or inst in taken):
			continue
		if kind == 'tree' and any(abs(cx - x) <= CHOP and abs(cy - y) <= CHOP for cx, cy in chopped):
			continue
		if kind in ('gold', 'stone', 'berries') and any(k == kind and abs(mx - x) <= PILE and abs(my - y) <= PILE for k, mx, my in mined):
			faded.setdefault(kind, []).append((x, y))
			continue
		left.setdefault(kind, []).append((x, y))
	return left, faded


def _draw(ax, rec, layers, side):
	from matplotlib import patheffects
	players = {p['number']: p for p in rec.get('players') or []}
	ax.set_axis_off()
	gx, gy = _turn([(0, 0), (side, 0), (side, side), (0, side)])
	ax.fill(gx, gy, color='#e9dfc8', zorder=0)
	ax.plot(gx + gx[:1], gy + gy[:1], color='#cbbd9f', linewidth=1.5, zorder=0)
	for kind, (colour, marker, area, alpha) in STYLE.items():
		if kind in layers.get('points', {}):
			xs, ys = _turn(layers['points'][kind])
			edge_colour = '#2a241b' if kind in ('livestock', 'relic') else 'none'
			ax.scatter(xs, ys, s=area, c=colour, marker=marker, alpha=alpha, linewidths=.5 if edge_colour != 'none' else 0,
				edgecolors=edge_colour, zorder=1)
	for kind, pts in layers.get('faded', {}).items():
		xs, ys = _turn(pts)
		ax.scatter(xs, ys, s=STYLE[kind][2], c=STYLE[kind][0], marker=STYLE[kind][1], alpha=.3, linewidths=0, zorder=1)
	for number, pts in layers.get('wall_dots', {}).items():
		xs, ys = _turn(pts)
		ax.scatter(xs, ys, s=3, c=_colour(players[number]), marker='s', linewidths=0, zorder=2)
	for p, x0, y0, x1, y1, name in layers.get('lines', []):
		xs, ys = _turn([(x0, y0), (x1, y1)])
		ax.plot(xs, ys, color=_colour(players[p]), linewidth=1.6 if 'Stone' in name or 'Fortified' in name else .9,
			linestyle=(0, (2, 1.5)) if 'Palisade' in name else '-', zorder=2)
	k = (120 / side) ** 2  # marker areas for a 1v1 map, smaller on bigger maps
	for p, x, y, name in layers.get('buildings', []):
		xs, ys = _turn([(x, y)])
		ax.scatter(xs, ys, s=BUILDING_SIZE.get(name, 18) * k, c=_colour(players[p]), marker='s', edgecolors='#2a241b',
			linewidths=.6 * k ** .5, alpha=.55 if name == 'Farm' else 1, zorder=3)
	for p, x, y, villager in layers.get('units', []):
		xs, ys = _turn([(x, y)])
		ax.scatter(xs, ys, s=(4 if villager else 14) * max(k, .5), c=_colour(players[p]), marker='o',
			edgecolors='none' if villager else '#2a241b', linewidths=.5, zorder=4)
	outline = [patheffects.withStroke(linewidth=3, foreground='#fffaf0')]
	for number, xy in layers.get('towns', {}).items():
		xs, ys = _turn([xy])
		if number in layers.get('towns_lost', ()):  # destroyed: a cross where it stood
			ax.scatter(xs, ys, s=110, c=_colour(players[number]), marker='X', edgecolors='#2a241b', linewidths=.8, zorder=3)
		else:
			ax.scatter(xs, ys, s=150, c=_colour(players[number]), marker='s', edgecolors='#2a241b', linewidths=1.2, zorder=3)
		ax.annotate(players[number]['name'], (xs[0], ys[0]), xytext=(0, 9), textcoords='offset points', ha='center',
			fontsize=9, fontweight='bold', color='#2a241b', path_effects=outline, zorder=5)
	ax.set_aspect('equal')
	ax.set_xlim(-side * 1.02, side * 1.02)
	ax.set_ylim(-2 * side * 1.01, side * .01)


def _colour(player):
	c = player.get('color_id')
	return PLAYER_COLORS[c % len(PLAYER_COLORS)] if isinstance(c, int) and c >= 0 else '#808080'
