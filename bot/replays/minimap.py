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
		end['towns'] = start['towns']  # names where each player started
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
		labels += ['Buildings', 'Army (last 3 min)', 'Villagers']
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
	""" Layers of the end of the game: buildings placed, walls, units' last positions, and where each player started. """
	names = reference().get('objects', {})
	players = {p['number']: p for p in rec.get('players') or []}
	end = result['duration']
	# Castles lost, deleted, or never seen built (placed, maybe never finished) aren't standing at the end.
	gone = [(c['player'], c['x'], c['y']) for c in result.get('castles') or [] if c.get('status') in ('lost', 'deleted', 'unclear')]
	buildings = []  # (player, x, y, name)
	lines = []      # (player, x0, y0, x1, y1, name)
	last = {}       # unit -> (t, player, x, y)
	for a in rec.get('actions') or []:
		p = a.get('player_id')
		if p not in players or a['t'] > end:
			continue
		x, y = a.get('x'), a.get('y')
		if a['a'] == 'BUILD' and x and x > 0:
			name = names.get(str(a.get('building_id'))) or ''
			buildings = [b for b in buildings if not (b[0] == p and abs(b[1] - x) < SAME_SPOT and abs(b[2] - y) < SAME_SPOT)]
			buildings.append((p, x, y, name))
		elif a['a'] == 'WALL' and x and x > 0 and a.get('x_end') is not None:
			lines.append((p, x, y, a['x_end'], a['y_end'], names.get(str(a.get('building_id'))) or ''))
		if a['a'] in UNIT_COMMANDS and x and x > 0:
			for i in a.get('object_ids') or []:
				last[i] = (a['t'], p, x, y)
	buildings = [b for b in buildings if not any(g[0] == b[0] and abs(g[1] - b[1]) < 3 and abs(g[2] - b[2]) < 3 for g in gone)]
	builders = {i for a in rec.get('actions') or [] if a['a'] in ('BUILD', 'WALL', 'REPAIR') for i in a.get('object_ids') or []}
	units = [(p, x, y, i in builders) for i, (t, p, x, y) in last.items() if t >= end - END_RECENT]
	return dict(buildings=buildings, lines=lines, units=units)


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
		ax.scatter(xs, ys, s=150, c=_colour(players[number]), marker='s', edgecolors='#2a241b', linewidths=1.2, zorder=3)
		ax.annotate(players[number]['name'], (xs[0], ys[0]), xytext=(0, 9), textcoords='offset points', ha='center',
			fontsize=9, fontweight='bold', color='#2a241b', path_effects=outline, zorder=5)
	ax.set_aspect('equal')
	ax.set_xlim(-side * 1.02, side * 1.02)
	ax.set_ylim(-2 * side * 1.01, side * .01)


def _colour(player):
	c = player.get('color_id')
	return PLAYER_COLORS[c % len(PLAYER_COLORS)] if isinstance(c, int) and c >= 0 else '#808080'
