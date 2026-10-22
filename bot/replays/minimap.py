# -*- coding: utf-8 -*-
"""
A picture of the starting map for a game's post: trees, gold, stone, berries, hunt, sheep, fish and relics where
the map generator put them, each player's starting Town Center and walls, drawn turned 45° like the in-game minimap.
Built from the objects in the recorded game's header (rec_parser.map_objects).
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


def render(rec, size=(9, 5.2), dpi=120):
	"""
	-> PNG bytes of the starting map, or None when the recording has no readable objects.
	rec: rec_parser.parse() output (players with number, name, color_id; map_objects).
	"""
	objects = rec.get('map_objects') or []
	if not objects:
		return None
	from matplotlib.figure import Figure
	from matplotlib.backends.backend_agg import FigureCanvasAgg
	from matplotlib import patheffects

	names = reference().get('objects', {})
	players = {p['number']: p for p in rec.get('players') or []}
	points, walls, towns = {}, {}, {}
	for owner, _, kind_id, x, y in objects:
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

	def turn(pts):  # tile (x, y) -> minimap position: the square map turned 45°
		return [x - y for x, y in pts], [-(x + y) for x, y in pts]

	fig = Figure(figsize=size, dpi=dpi)
	FigureCanvasAgg(fig)
	fig.patch.set_facecolor('#2b2d31')  # Discord's dark background, so the corners blend in
	ax = fig.add_axes([0.01, 0.07, 0.98, 0.92])
	ax.set_axis_off()
	gx, gy = turn([(0, 0), (side, 0), (side, side), (0, side)])
	ax.fill(gx, gy, color='#e9dfc8', zorder=0)
	ax.plot(gx + gx[:1], gy + gy[:1], color='#cbbd9f', linewidth=1.5, zorder=0)
	for kind, (colour, marker, area, alpha) in STYLE.items():
		if kind in points:
			xs, ys = turn(points[kind])
			edge_colour = '#2a241b' if kind in ('livestock', 'relic') else 'none'
			ax.scatter(xs, ys, s=area, c=colour, marker=marker, alpha=alpha, linewidths=.5 if edge_colour != 'none' else 0,
				edgecolors=edge_colour, zorder=1)
	outline = [patheffects.withStroke(linewidth=3, foreground='#fffaf0')]
	for number, pts in walls.items():
		xs, ys = turn(pts)
		ax.scatter(xs, ys, s=3, c=_colour(players[number]), marker='s', linewidths=0, zorder=2)
	for number, xy in towns.items():
		xs, ys = turn([xy])
		ax.scatter(xs, ys, s=150, c=_colour(players[number]), marker='s', edgecolors='#2a241b', linewidths=1.2, zorder=3)
		ax.annotate(players[number]['name'], (xs[0], ys[0]), xytext=(0, 9), textcoords='offset points', ha='center',
			fontsize=9, fontweight='bold', color='#2a241b', path_effects=outline, zorder=4)
	ax.set_aspect('equal')
	ax.set_xlim(-side * 1.02, side * 1.02)
	ax.set_ylim(-2 * side * 1.01, side * .01)
	shown = [k for k in STYLE if k in points]
	handles = [ax.scatter([], [], s=28 if k != 'tree' else 18, c=STYLE[k][0], marker=STYLE[k][1],
		edgecolors='#2a241b' if k in ('livestock', 'relic') else 'none') for k in shown]
	fig.legend(handles, [LABELS[k] for k in shown], loc='lower center', ncol=len(shown), frameon=False, fontsize=8.5,
		labelcolor='#dbdee1', handletextpad=.2, columnspacing=1.2)
	buf = io.BytesIO()
	fig.savefig(buf, format='png', facecolor=fig.get_facecolor())
	return buf.getvalue()


def _colour(player):
	c = player.get('color_id')
	return PLAYER_COLORS[c % len(PLAYER_COLORS)] if isinstance(c, int) and c >= 0 else '#808080'
