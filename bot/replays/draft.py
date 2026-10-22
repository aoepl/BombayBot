# -*- coding: utf-8 -*-
"""
Draft logic: which civ to pick next for a map, from the games the bot has analysed and the civs' tech trees.

/aoe draft (civ_draft) uses it once a match has started. With few games saved most ratings stay close to "average" on purpose:
every estimate is pulled towards a broader one by how few games back it up. Community votes (the civ voting
sheet) can be plugged in later through `Prior`.

Everything is measured against Elo, so a civ is not rated well just because good players picked it:
- a game result is compared with the win chance the two teams' Elos gave (insights' Elo scale: the spread of
  Elos in that lobby sets how much a gap means);
- a fight is compared with the share of army value the Elos of the players in it predicted (allies who joined
  the fight count, so one player holding two armies is judged 1 against 2) — analysis.fight_records().

Pieces, on a 0..1 scale where 0.5 is average:
- strength(civ, seat, map): how much better than Elo the civ does. Games on this exact map count most, then
  the same map type, then the same seat anywhere, then other seats; with few games it stays near the tech
  tree's guess (how complete the civ's best lines for the seat are).
- army(civ, seat, map): the unit lines the civ fields (from saved fights; the tech tree's best lines for the
  seat until there are some).
- line_matchup(line, enemy line): a cost-for-cost estimate from unit stats with the civs' own upgrades
  (techtree.counter), corrected by how fights between those lines actually went against Elo.
- matchup(civ, seat, enemy civ, enemy seat, map): the two usual armies against each other plus how well each
  civ can answer the other's army with its own best counter; corrected by real fights between the civs and by
  lost bases (a base taken by a civ whose team still lost the game counts more).
- elo_fit(civ, elo): how the civ does for players in this Elo range compared with all its games.
- player_fit(player, civ): how this player does with this civ compared with their own other games.

recommend() ranks the possible next picks; simulate() plays a whole draft with both teams picking the best
available; balance() says how even the finished draft is. Any format works: bans, pick order, seats, players.
"""
import json
import math
import random
import re
from collections import defaultdict
from itertools import combinations, permutations
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import techtree

# Strength: a game counts more the closer it is to the pick (same seat, same map type, same map), and every
# estimate starts with PRIOR_GAMES games' worth of the tech tree's guess.
MAP_WEIGHT, CATEGORY_WEIGHT, SEAT_WEIGHT, OTHER_WEIGHT = 1.0, 0.5, 0.2, 0.1
PRIOR_GAMES = 6
MATCHUP_PRIOR = 4        # lost bases worth of "the armies' estimate" every matchup starts with
LINE_FIGHT_PRIOR = 15    # fight weight (about 15 big fights) of the stat estimate a line matchup starts with
CIV_FIGHT_PRIOR = 8      # ... and a civ pair matchup
ELO_FIT_PRIOR = 6
PLAYER_PRIOR = 4
LOSING_SIDE_BONUS = 1.5  # a base taken by a civ whose team lost the game counts 1.5x
WIN_WEIGHT = 0.6         # one game: 60% result against Elo, 40% fights against Elo
FIGHT_TO_GAME = 2.0      # fights' share-vs-Elo is about half as spread out as results; scaled to match
SAME_SEAT_FACE = 1.0     # how much a matchup against the enemy in the same kind of seat counts
CROSS_SEAT_FACE = 0.5    # ... and against the other kind of seat
MATCHUP_WEIGHT = 0.5     # matchups' share of a pick's value next to strength
ELO_FIT_WEIGHT = 0.3
PLAYER_FIT_WEIGHT = 0.3
TECH_WEIGHT = 0.25       # how much the tech tree's seat fit sets a civ's starting strength
FIELD_SIZE = 6           # an open enemy slot is judged as its 6 likeliest picks ...
OPEN_SLOT_FACE = 0.5     # ... at half the weight of a civ already picked
DENIAL_WEIGHT = 0.5      # how much taking a civ away from the enemy is worth next to its value for us
USUAL_SHARE = 0.6        # matchup: the usual armies against each other ...
ANSWER_SHARE = 0.4       # ... and how well each side can switch to a counter
# Closed maps: walls and space to build up let siege, gunpowder, towers and expensive power units come out, so
# civs strong in those do better there. How much that counts per map type, and its weight next to Elo results.
CLOSED_LEAN = {'Closed': 1.0, 'Semi Closed': 0.6}
CLOSED_WEIGHT = 0.3
SIEGE_LINES = ('Bombard Cannons', 'Mangonels', 'Rams', 'Scorpions')
TOWER_TECHS = ('Bombard Tower', 'Keep', 'Guard Tower', 'Masonry', 'Architecture', 'Heated Shot', 'Murder Holes', 'Arson',
	'Hoardings', 'Treadmill Crane')
GUNPOWDER_CLASS = '23'  # armour class of gunpowder units in the game data
POWER_COST = 150        # a land unit costing this much or more (elephants, war wagons ...) is a power unit
# Civ bonuses the tech tree data can't show, that make their siege or gunpowder better than the units alone say.
# Edit freely; a bonus makes up BONUS_SHARE of that part of the closed-map score, units and upgrades the rest.
CLOSED_BONUS = {
	'Celts': 'siege', 'Mongols': 'siege', 'Ethiopians': 'siege', 'Koreans': 'siege', 'Slavs': 'siege',
	'Spanish': 'gunpowder', 'Turks': 'gunpowder', 'Bohemians': 'gunpowder', 'Hindustanis': 'gunpowder',
	'Portuguese': 'gunpowder',
}
BONUS_SHARE = 0.25      # how much a bonus adds to that part (0..1)
# Hybrid maps (land and water): a strong navy and fishing, and wood bonuses, count; a cavalry civ in the pocket
# that can fish boom is very strong. How much that counts per map type, and its weight next to Elo results.
HYBRID_LEAN = {'Hybrid': 1.0}
HYBRID_WEIGHT = 0.3
NAVY_LINES = ('Galleys', 'Fire ships', 'Demo ships', 'Cannon Galleons')
DOCK_TECHS = ('Gillnets', 'Careening', 'Dry Dock', 'Shipwright')
WOOD_TECHS = ('Double-Bit Axe', 'Bow Saw', 'Two-Man Saw')
# Civ bonuses the data can't show: 'water' (cheaper or better fishing, docks or warships), 'wood' (faster or
# cheaper wood, or saving it). Edit freely; like CLOSED_BONUS, a bonus makes up BONUS_SHARE of that part.
HYBRID_BONUS = {
	'Japanese': 'water', 'Malay': 'water', 'Italians': 'water', 'Vikings': 'water', 'Dravidians': 'water',
	'Portuguese': 'water', 'Byzantines': 'water', 'Berbers': 'water',
	'Celts': 'wood', 'Burmese': 'wood', 'Huns': 'wood', 'Mayans': 'wood',
}
ELO_RANGES = ((0, 1400), (1400, 1600), (1600, 1800), (1800, 99999))
CHRONICLES = {'Achaemenids', 'Athenians', 'Spartans'}  # not in ranked games

# How much each kind of seat leans on a unit line, before the civ's version of it is considered.
SEAT_LINES = {
	'Flank': {'Archers': 1.0, 'Skirmishers': 0.6, 'Eagles': 0.9, 'Scouts/Hussars': 0.5, 'Knights': 0.45,
		'Spearmen/Halbs': 0.4, 'Militia line': 0.35, 'Cavalry Archers': 0.4, 'Camels': 0.3, 'Hand Cannoneers': 0.3,
		'Mangonels': 0.3, 'Monks': 0.15},
	'Pocket': {'Knights': 1.0, 'Cavalry Archers': 0.75, 'Scouts/Hussars': 0.6, 'Camels': 0.55, 'Elephants': 0.7,
		'Steppe Lancers': 0.6, 'Spearmen/Halbs': 0.3, 'Archers': 0.3, 'Hand Cannoneers': 0.35, 'Mangonels': 0.2,
		'Eagles': 0.6, 'Monks': 0.15},
}
UNIQUE_BONUS = 1.15  # a unique unit counts like the standard line of its kind for the seat, a bit more
SUPPORT_LINES = {'Rams', 'Mangonels', 'Scorpions', 'Bombard Cannons', 'Monks'}
SUPPORT_SHARE = 0.5  # siege and monks back an army up; they count half in the army a civ fields
LINES_KEPT = 3


def _norm(text):
	return (text or '').lower().replace(' ', '').replace('_', '')


def _logistic(x):
	return 1 / (1 + math.exp(-x))


def _elo_scale(elos):
	"""
	Same as analysis.elo_scale: Elo points that make a side 10x as strong, from the lobby's spread (a gap of one
	standard deviation, at least 100, is a 62% favourite).
	"""
	elos = [e for e in elos if e is not None]
	if len(elos) < 2:
		return 400.0
	mean = sum(elos) / len(elos)
	sd = max(100.0, math.sqrt(sum((e - mean) ** 2 for e in elos) / len(elos)))
	return sd / -math.log10(1 / 0.62 - 1)


def team_expectation(elos_a, elos_b, scale=None):
	""" Win chance of team a from Elo: strength is the sum of 10^(elo/scale) of its players. """
	scale = scale or _elo_scale(list(elos_a) + list(elos_b))
	sa = sum(10 ** (e / scale) for e in elos_a)
	sb = sum(10 ** (e / scale) for e in elos_b)
	return sa / (sa + sb) if sa + sb else 0.5


def _lines(text):
	""" {unit: count} saved with a fight (JSON), as {line: army value}. """
	try:
		units = json.loads(text) if isinstance(text, str) else (text or {})
	except ValueError:
		return {}
	out = defaultdict(float)
	for name, n in units.items():
		s = techtree.unit(name)
		if s is None or name in techtree.NOT_ARMY:
			continue
		out[techtree.line_of(name)] += n * techtree._cost(s)
	return dict(out)


def _shares(values):
	total = sum(values.values())
	return {k: v / total for k, v in values.items()} if total else {}


_FORTRESS = {}


def fortress(civ):
	"""
	How well a civ can use a closed map, from its tech tree, each 0..1: siege (Bombard Cannons, Onagers, Rams,
	Scorpions with Siege Engineers and Chemistry), gunpowder (its gunpowder lines, unique ones too: two good lines
	count as full), towers (Bombard Tower, Keep and tower upgrades) and power (its best expensive land unit line).
	score weighs them 35 / 35 / 15 / 15. Civ bonuses missing from the data come from CLOSED_BONUS.
	"""
	if civ in _FORTRESS:
		return _FORTRESS[civ]
	techs = set(techtree.data()['civs'].get(civ, {}).get('techs', []))
	lines = techtree.civ_lines(civ)
	siege = sum(techtree.civ_line_quality(civ, ln) for ln in SIEGE_LINES) / len(SIEGE_LINES)
	gun, power = [], []
	for ln in lines:
		u = techtree.unit(techtree._best_unit(civ, ln)) or {}
		if GUNPOWDER_CLASS in (u.get('armours') or {}) and ln not in ('Cannon Galleons',) and u.get('speed', 0) > 0:
			gun.append(techtree.civ_line_quality(civ, ln))
		cost = sum((u.get('cost') or {}).values())
		if GUNPOWDER_CLASS in (u.get('armours') or {}):
			continue  # counted as gunpowder already
		if POWER_COST <= cost < techtree.HERO_COST and ln not in SIEGE_LINES and u.get('speed', 0) > 0 \
				and not ln.endswith(('ships', 'Galleons', 'Galleys', 'Hulks')) and techtree.line_of(ln) not in SUPPORT_LINES:
			power.append(techtree.civ_line_quality(civ, ln))
	gunpowder = min(1.0, sum(sorted(gun, reverse=True)[:2]) / 2) if gun else 0.0
	# Units and upgrades make up most of siege and gunpowder; a known civ bonus makes up the rest.
	bonus = CLOSED_BONUS.get(civ)
	siege = (1 - BONUS_SHARE) * siege + (BONUS_SHARE if bonus == 'siege' else 0.0)
	gunpowder = (1 - BONUS_SHARE) * gunpowder + (BONUS_SHARE if bonus == 'gunpowder' and gun else 0.0)
	towers = sum(1 for t in TOWER_TECHS if t in techs) / len(TOWER_TECHS)
	best_power = max(power, default=0.0)
	out = dict(siege=siege, gunpowder=gunpowder, towers=towers, power=best_power,
		score=0.35 * siege + 0.35 * gunpowder + 0.15 * towers + 0.15 * best_power)
	_FORTRESS[civ] = out
	return out


_HYBRID = {}


def hybrid(civ, seat=None):
	"""
	How well a civ can use a hybrid map, each part 0..1: water (navy lines and dock techs: Gillnets, Careening, Dry
	Dock, Shipwright), wood (lumber upgrades) and, for pockets, cavalry (its best melee cavalry line) that the fish
	boom pays for. Known civ bonuses from HYBRID_BONUS make up part of water or wood.
	score: flanks 55 water / 45 wood; pockets 35 water / 20 wood / 45 cavalry x fishing.
	"""
	key = (civ, seat)
	if key in _HYBRID:
		return _HYBRID[key]
	techs = set(techtree.data()['civs'].get(civ, {}).get('techs', []))
	navy = sum(techtree.civ_line_quality(civ, ln) for ln in NAVY_LINES) / len(NAVY_LINES)
	docks = sum(1 for t in DOCK_TECHS if t in techs) / len(DOCK_TECHS)
	bonus = HYBRID_BONUS.get(civ)
	water = (1 - BONUS_SHARE) * (0.6 * navy + 0.4 * docks) + (BONUS_SHARE if bonus == 'water' else 0.0)
	wood = (1 - BONUS_SHARE) * (sum(1 for t in WOOD_TECHS if t in techs) / len(WOOD_TECHS)) + \
		(BONUS_SHARE if bonus == 'wood' else 0.0)
	cav = 0.0
	for ln in techtree.civ_lines(civ):
		u = techtree.unit(techtree._best_unit(civ, ln)) or {}
		if u and techtree._cavalry(techtree._best_unit(civ, ln)) and not techtree._ranged(techtree._best_unit(civ, ln)) \
				and ln != 'Scouts/Hussars' and sum((u.get('cost') or {}).values()) < techtree.HERO_COST:
			cav = max(cav, techtree.civ_line_quality(civ, ln))
	cavalry = cav * (0.5 + 0.5 * water)  # the fish boom is what pays for the cavalry
	if seat == 'Pocket':
		score = 0.35 * water + 0.2 * wood + 0.45 * cavalry
	else:
		score = 0.55 * water + 0.45 * wood
	out = dict(water=water, wood=wood, cavalry=cavalry if seat == 'Pocket' else 0.0, score=score)
	_HYBRID[key] = out
	return out


def _top_quarter_hybrid(key, seat):
	vals = sorted((hybrid(c, seat)[key] for c in ranked_civs()), reverse=True)
	return vals[len(vals) // 4]


class Prior:
	""" Starting rating for a civ in a seat on a map type, 0..1. Neutral now; the voting sheet can feed this later. """

	def __init__(self, table: Optional[Dict] = None):
		self.table = table or {}  # {(civ, seat, map_category): 0..1}

	def __call__(self, civ, seat, category):
		return self.table.get((civ, seat, category))


@dataclass
class Slot:
	""" One player position in a team: seat ('Flank' / 'Pocket'), the player's BombayBot Elo, and their name. """
	seat: Optional[str]
	elo: Optional[int] = None
	name: Optional[str] = None
	civ: Optional[str] = None  # filled in once picked
	user_id: Optional[int] = None


@dataclass
class Draft:
	map_name: str
	map_category: Optional[str]
	available: List[str]                  # civs left after bans
	teams: List[List[Slot]]               # teams[0], teams[1]
	order: List[int] = field(default_factory=list)  # team index of each pick, e.g. [0, 1, 1, 0, 0, 1, 1, 0]
	picks: List[tuple] = field(default_factory=list)  # (team, slot index, civ) made so far
	bans: List[str] = field(default_factory=list)


class Model:
	"""
	Ratings learned from saved games. rows: aoe_game_players rows; matchups: aoe_matchups rows (lost bases);
	fights: aoe_fights rows.
	"""

	def __init__(self, rows, matchups=(), fights=(), prior: Callable = None):
		self.prior = prior or Prior()
		self.rows = [dict(r) for r in rows if r.get('civ')]
		self.matchups = list(matchups)
		self.fights = list(fights)
		self._results_vs_elo()
		self._learn_fights()
		self._cache = {}

	# ----- inputs measured against Elo
	def _results_vs_elo(self):
		""" Each row gets 'resid': how much better than its Elo the player's game went (about -0.6..+0.6). """
		games = defaultdict(list)
		for r in self.rows:
			games[r.get('game_key')].append(r)
		for rs in games.values():
			teams = defaultdict(list)
			for r in rs:
				teams[r.get('team')].append(r)
			known = all(r.get('elo') is not None for r in rs)
			scale = _elo_scale([r.get('elo') for r in rs])
			for r in rs:
				if r.get('won') is None:
					r['resid'] = None
					continue
				mine = [x['elo'] for x in teams[r.get('team')]] if known else []
				theirs = [x['elo'] for t, xs in teams.items() if t != r.get('team') for x in xs] if known else []
				expected = team_expectation(mine, theirs, scale) if known and theirs else 0.5
				result = (1.0 if r['won'] else 0.0) - expected
				fight = r.get('fight_vs_elo')
				if fight is not None:
					r['resid'] = WIN_WEIGHT * result + (1 - WIN_WEIGHT) * max(-0.5, min(0.5, FIGHT_TO_GAME * fight))
				else:
					r['resid'] = result

	def _learn_fights(self):
		"""
		From saved fights: (1) results by unit line pair, against Elo; (2) results by civ pair; (3) which lines
		each civ fields, per seat. A fight's weight grows with its size (square root of army value killed).
		"""
		self.line_fights = defaultdict(lambda: [0.0, 0.0])   # (line_a, line_b) -> [weighted result, weight]
		self.civ_fights = defaultdict(lambda: [0.0, 0.0, 0])  # (civ_a, seat_a, civ_b, seat_b) -> [.., .., fights]
		self.fielded = defaultdict(lambda: defaultdict(float))  # (civ, seat) -> line -> army value
		for f in self.fights:
			la, lb = _lines(f.get('a_lines')), _lines(f.get('b_lines'))
			size = (f.get('a_killed') or 0) + (f.get('b_killed') or 0)
			if size <= 0:
				continue
			w = math.sqrt(size / 1000)
			expected = f.get('expected') if f.get('expected') is not None else 0.5
			res = (f.get('a_share') if f.get('a_share') is not None else 0.5) - expected
			sa, sb = _shares(la), _shares(lb)
			for x, px in sa.items():
				for y, py in sb.items():
					self.line_fights[(x, y)][0] += w * px * py * res
					self.line_fights[(x, y)][1] += w * px * py
					self.line_fights[(y, x)][0] -= w * px * py * res
					self.line_fights[(y, x)][1] += w * px * py
			key, rev = (f['a_civ'], f.get('a_seat'), f['b_civ'], f.get('b_seat')), (f['b_civ'], f.get('b_seat'), f['a_civ'], f.get('a_seat'))
			for k, sign in ((key, 1), (rev, -1)):
				self.civ_fights[k][0] += sign * w * res
				self.civ_fights[k][1] += w
				self.civ_fights[k][2] += 1
			for civ, seat, lines in ((f['a_civ'], f.get('a_seat'), la), (f['b_civ'], f.get('b_seat'), lb)):
				for line, v in lines.items():
					s = techtree.unit(techtree._best_unit(civ, line)) or {}
					support = line in SUPPORT_LINES or bool(s.get('blast') and s.get('range', 0) >= 5)
					self.fielded[(civ, seat)][line] += v * (SUPPORT_SHARE if support else 1)

	# ----- strength
	@staticmethod
	def _closeness(r, seat, map_name, category):
		""" How much a game says about this civ, seat and map: same map 1, same map type 0.5, same seat elsewhere 0.2, other seat 0.1. """
		if seat and r.get('seat') != seat:
			return OTHER_WEIGHT
		if _norm(r.get('map_name')) == _norm(map_name):
			return MAP_WEIGHT
		if category and r.get('map_category') == category:
			return CATEGORY_WEIGHT
		return SEAT_WEIGHT

	def _seat_fit(self, civ, seat):
		""" How well the civ's best lines suit the seat: line quality times how much the seat leans on that kind. """
		army = self.default_army(civ, seat)
		lean = SEAT_LINES.get(seat) or {}
		total = 0.0
		for line, share in army.items():
			total += share * techtree.civ_line_quality(civ, line) * (lean.get(self._kind(civ, line), 0.5) if lean else 0.7)
		return total

	def _kind(self, civ, line):
		""" The standard line a unique line plays like (for seat leans). """
		if line in techtree.LINES:
			return line
		u = techtree._best_unit(civ, line)
		return ('Cavalry Archers' if techtree._is_cav_archer(u) else 'Archers' if techtree._ranged(u)
			else 'Knights' if techtree._cavalry(u) else 'Militia line')

	def tech_strength(self, civ, seat):
		""" Small head start from the tech tree: the civ's seat fit compared with the median civ's. """
		key = ('median_fit', seat)
		if key not in self._cache:
			fits = sorted(self._seat_fit(c, seat) for c in ranked_civs())
			self._cache[key] = fits[len(fits) // 2]
		return TECH_WEIGHT * (self._seat_fit(civ, seat) - self._cache[key])

	def closed_strength(self, civ, category):
		""" Head start on closed maps for strong siege, gunpowder, towers and power units (0 on open maps). """
		lean = CLOSED_LEAN.get(category, 0.0)
		if not lean:
			return 0.0
		if 'median_fortress' not in self._cache:
			scores = sorted(fortress(c)['score'] for c in ranked_civs())
			self._cache['median_fortress'] = scores[len(scores) // 2]
		return lean * CLOSED_WEIGHT * (fortress(civ)['score'] - self._cache['median_fortress'])

	def hybrid_strength(self, civ, seat, category):
		""" Head start on hybrid maps for navy and fishing, wood, and (pockets) cavalry with a fish boom. """
		lean = HYBRID_LEAN.get(category, 0.0)
		if not lean:
			return 0.0
		key = ('median_hybrid', seat)
		if key not in self._cache:
			scores = sorted(hybrid(c, seat)['score'] for c in ranked_civs())
			self._cache[key] = scores[len(scores) // 2]
		return lean * HYBRID_WEIGHT * (hybrid(civ, seat)['score'] - self._cache[key])

	def strength(self, civ, seat, map_name, category=None):
		""" (rating 0..1, games on this map, games in all) for the civ in this seat on this map. """
		key = ('strength', civ, seat, _norm(map_name), category)
		if key in self._cache:
			return self._cache[key]
		mine = [r for r in self.rows if r['civ'] == civ and r.get('resid') is not None]
		base = self.tech_strength(civ, seat) + self.closed_strength(civ, category) + self.hybrid_strength(civ, seat, category)
		prior = self.prior(civ, seat, category)
		if prior is not None:
			base = prior - 0.5
		total = weight = 0.0
		for r in mine:
			w = self._closeness(r, seat, map_name, category)
			total += w * r['resid']
			weight += w
		est = (total + base * PRIOR_GAMES) / (weight + PRIOR_GAMES)
		n_map = sum(1 for r in mine if _norm(r.get('map_name')) == _norm(map_name))
		n_all = len(mine)
		out = (0.5 + est, n_map, n_all)
		self._cache[key] = out
		return out

	# ----- armies
	def default_army(self, civ, seat):
		""" {line: share} of the civ's best lines for the seat by the tech tree (line quality squared x seat lean). """
		key = ('default_army', civ, seat)
		if key in self._cache:
			return self._cache[key]
		lean = SEAT_LINES.get(seat) or {k: max(SEAT_LINES['Flank'].get(k, 0), SEAT_LINES['Pocket'].get(k, 0))
			for k in set(SEAT_LINES['Flank']) | set(SEAT_LINES['Pocket'])}
		scores = {}
		for line, units in techtree.civ_lines(civ).items():
			if line in techtree.LINES:
				weight = lean.get(line, 0)
			else:
				s = techtree.unit(units[-1]) or {}
				naval = '16' in s.get('armours', {})
				weight = 0 if naval else UNIQUE_BONUS * lean.get(self._kind(civ, line), 0.3) * (0.5 if s.get('blast') else 1)
			if weight:
				scores[line] = weight * techtree.civ_line_quality(civ, line) ** 2
		top = dict(sorted(scores.items(), key=lambda kv: -kv[1])[:LINES_KEPT])
		out = _shares(top)
		self._cache[key] = out
		return out

	def army(self, civ, seat):
		""" {line: share} the civ fields in this seat: saved fights, starting from the tech tree's guess. """
		key = ('army', civ, seat)
		if key in self._cache:
			return self._cache[key]
		default = self.default_army(civ, seat)
		seen = dict(self.fielded.get((civ, seat), {}))
		total = sum(seen.values())
		weight = min(1.0, total / 20000)  # ~20k army value fielded = trust what they built over the tech tree
		mix = defaultdict(float)
		for line, share in default.items():
			mix[line] += (1 - weight) * share
		for line, share in _shares(seen).items():
			mix[line] += weight * share
		top = dict(sorted(mix.items(), key=lambda kv: -kv[1])[:LINES_KEPT + 1])
		out = _shares(top)
		self._cache[key] = out
		return out

	# ----- unit line matchups
	def line_matchup(self, line_a, line_b, civ_a=None, civ_b=None):
		""" 0..1 for a cost-for-cost army of line_a against line_b: unit stats, corrected by real fights. """
		prior = techtree.counter(line_a, line_b, civ_a, civ_b)
		total, weight = self.line_fights.get((line_a, line_b), (0.0, 0.0))
		adj = total / (weight + LINE_FIGHT_PRIOR) if weight else 0.0
		return max(0.02, min(0.98, prior + adj))

	def _best_answer(self, civ, enemy_line, enemy_civ):
		""" The civ's best line against one enemy line (lines it has in decent shape only). """
		best = (0.5, None)
		for line in techtree.civ_lines(civ):
			if line in SUPPORT_LINES or (line in techtree.LINES and line not in SEAT_LINES['Flank'] and line not in SEAT_LINES['Pocket']):
				continue  # ships, siege and monks
			if techtree.civ_line_quality(civ, line) < 0.6:
				continue
			v = self.line_matchup(line, enemy_line, civ, enemy_civ)
			if v > best[0]:
				best = (v, line)
		return best

	@staticmethod
	def _fighting(army):
		""" The army without siege and monks (their stat-based counters mean little), unless that is all there is. """
		main = {k: v for k, v in army.items() if k not in SUPPORT_LINES}
		return _shares(main) or army

	def army_matchup(self, civ, seat, enemy_civ, enemy_seat):
		""" (0..1, note): usual army against usual army, and how well each side can switch to a counter. """
		key = ('army_matchup', civ, seat, enemy_civ, enemy_seat)
		if key in self._cache:
			return self._cache[key]
		mine, theirs = self._fighting(self.army(civ, seat)), self._fighting(self.army(enemy_civ, enemy_seat))
		usual = sum(pa * pb * self.line_matchup(la, lb, civ, enemy_civ)
			for la, pa in mine.items() for lb, pb in theirs.items()) if mine and theirs else 0.5
		answer_mine = sum(pb * self._best_answer(civ, lb, enemy_civ)[0] for lb, pb in theirs.items()) if theirs else 0.5
		answer_theirs = sum(pa * self._best_answer(enemy_civ, la, civ)[0] for la, pa in mine.items()) if mine else 0.5
		answer = 0.5 + (answer_mine - answer_theirs) / 2
		value = USUAL_SHARE * usual + ANSWER_SHARE * answer
		top = lambda a: " + ".join(k for k, _ in sorted(a.items(), key=lambda kv: -kv[1])[:2])  # noqa: E731
		out = (value, f"{top(mine)} vs {top(theirs)}")
		self._cache[key] = out
		return out

	# ----- civ matchups
	def matchup(self, civ, seat, enemy_civ, enemy_seat, map_name, category=None):
		""" (rating 0..1 for civ against enemy_civ, real fights + lost bases behind it, note). 0.5 = even. """
		key = ('matchup', civ, seat, enemy_civ, enemy_seat, _norm(map_name), category)
		if key in self._cache:
			return self._cache[key]
		est, note = self.army_matchup(civ, seat, enemy_civ, enemy_seat)
		total, weight, n_fights = self.civ_fights.get((civ, seat, enemy_civ, enemy_seat), (0.0, 0.0, 0))
		if weight:
			est += total / (weight + CIV_FIGHT_PRIOR)
		score = w_sum = 0.0
		bases = 0
		for m in self.matchups:
			if _norm(m.get('map_name')) == _norm(map_name):
				w = 1.0
			elif category and m.get('map_category') == category:
				w = 0.5
			else:
				w = 0.2
			won_it = (m['winner_civ'], m.get('winner_seat'), m['loser_civ'], m.get('loser_seat')) == (civ, seat, enemy_civ, enemy_seat)
			lost_it = (m['winner_civ'], m.get('winner_seat'), m['loser_civ'], m.get('loser_seat')) == (enemy_civ, enemy_seat, civ, seat)
			if not (won_it or lost_it):
				continue
			if m.get('winner_team_won') is False:
				w *= LOSING_SIDE_BONUS  # took the base even though their team lost: a strong sign
			score += w * (1.0 if won_it else 0.0)
			w_sum += w
			bases += 1
		if w_sum:
			est = (score + est * MATCHUP_PRIOR) / (w_sum + MATCHUP_PRIOR)
		out = (max(0.02, min(0.98, est)), n_fights + bases, note)
		self._cache[key] = out
		return out

	# ----- fits
	def elo_fit(self, civ, elo):
		""" How the civ does against Elo for players in this Elo range, compared with all its games (0.5 = same). """
		if elo is None:
			return 0.5, 0
		band = next(((lo, hi) for lo, hi in ELO_RANGES if lo <= elo < hi), None)
		mine = [r for r in self.rows if r['civ'] == civ and r.get('elo') is not None and r.get('resid') is not None]
		in_band = [r['resid'] for r in mine if band and band[0] <= r['elo'] < band[1]]
		if not in_band:
			return 0.5, 0
		overall = sum(r['resid'] for r in mine) / len(mine)
		return 0.5 + sum(v - overall for v in in_band) / (len(in_band) + ELO_FIT_PRIOR), len(in_band)

	def player_fit(self, slot: Slot, civ):
		""" How this player does with this civ compared with their own other games (0.5 = same). """
		def is_them(r):
			return (slot.user_id and r.get('user_id') == slot.user_id) or (slot.name and _norm(r.get('name')) == _norm(slot.name))
		theirs = [r for r in self.rows if is_them(r) and r.get('resid') is not None]
		with_civ = [r['resid'] for r in theirs if r['civ'] == civ]
		if not with_civ:
			return 0.5, 0
		overall = sum(r['resid'] for r in theirs) / len(theirs)
		return 0.5 + sum(v - overall for v in with_civ) / (len(with_civ) + PLAYER_PRIOR), len(with_civ)

	# ----- value of one pick
	def likely(self, slot: Slot, left, map_name, category=None):
		""" The civs an open enemy slot is most likely to get: the strongest left for its seat. """
		key = ('likely', slot.seat, tuple(left), _norm(map_name), category)
		if key not in self._cache:
			ranked = sorted(left, key=lambda c: -self.strength(c, slot.seat, map_name, category)[0])
			self._cache[key] = ranked[:FIELD_SIZE]
		return self._cache[key]

	def value(self, civ, slot: Slot, enemies: List[Slot], map_name, category=None, left=None):
		"""
		(value, reasons) of giving `civ` to `slot`, given the enemy civs picked so far. With `left` (civs still
		available), an enemy slot not picked yet counts as its likely picks, at half weight.
		"""
		strength, n_map, n_all = self.strength(civ, slot.seat, map_name, category)
		fit, fit_games = self.elo_fit(civ, slot.elo)
		pfit, p_games = self.player_fit(slot, civ)
		vs, vs_weight, vs_notes = 0.0, 0.0, []
		for e in enemies:
			face = SAME_SEAT_FACE if e.seat == slot.seat else CROSS_SEAT_FACE
			if not e.civ:
				field_civs = [c for c in self.likely(e, left, map_name, category) if c != civ] if left else []
				for c in field_civs:
					m, _, _ = self.matchup(civ, slot.seat, c, e.seat, map_name, category)
					vs += OPEN_SLOT_FACE * face * (m - 0.5) / len(field_civs)
				if field_civs:
					vs_weight += OPEN_SLOT_FACE * face
				continue
			m, n, note = self.matchup(civ, slot.seat, e.civ, e.seat, map_name, category)
			vs += face * (m - 0.5)
			vs_weight += face
			if abs(m - 0.5) >= 0.03 or n:
				vs_notes.append((abs(m - 0.5) * face, f"vs {e.civ} {e.seat or ''} {m:.0%} ({note}" + (f", {n} real" if n else "") + ")"))
		matchup_part = vs / vs_weight if vs_weight else 0.0
		value = strength + MATCHUP_WEIGHT * matchup_part + ELO_FIT_WEIGHT * (fit - 0.5) + PLAYER_FIT_WEIGHT * (pfit - 0.5)
		army = self.army(civ, slot.seat)
		reasons = [f"{strength:.0%} vs Elo as {slot.seat or 'player'} ({n_map} game{'s' if n_map != 1 else ''} on this map, "
			f"{n_all} in all)", "army " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(army.items(), key=lambda kv: -kv[1]))]
		if fit_games:
			reasons.append(f"Elo range fit {fit - 0.5:+.0%} ({fit_games} game{'s' if fit_games != 1 else ''})")
		if p_games:
			reasons.append(f"{slot.name} with {civ} {pfit - 0.5:+.0%} ({p_games} game{'s' if p_games != 1 else ''})")
		reasons += [n for _, n in sorted(vs_notes, key=lambda x: -x[0])[:2]]
		return value, reasons


def _open_slots(team):
	return [i for i, s in enumerate(team) if not s.civ]


def _apply(draft: Draft):
	teams = [[Slot(s.seat, s.elo, s.name, s.civ, s.user_id) for s in t] for t in draft.teams]
	for team, idx, civ in draft.picks:
		teams[team][idx].civ = civ
	taken = {civ for _, _, civ in draft.picks}
	return teams, [c for c in draft.available if c not in taken and c not in draft.bans]


def recommend(model: Model, draft: Draft, team: int = None, top: int = 5):
	"""
	Ranked options for the next pick: dicts of civ, slot, value for us, denial (how much the best enemy slot
	would lose if we take it) and reasons. team defaults to whoever picks next in draft.order.
	"""
	teams, left = _apply(draft)
	if team is None:
		team = draft.order[len(draft.picks)] if len(draft.picks) < len(draft.order) else 0
	enemy = 1 - team
	enemy_slots = _open_slots(teams[enemy])
	# The enemy's value for every civ left, per open slot (to see what taking a civ denies them).
	enemy_values = {i: {c: model.value(c, teams[enemy][i], teams[team], draft.map_name, draft.map_category, left)[0] for c in left}
		for i in enemy_slots}
	options = []
	for civ in left:
		denial = 0.0
		for i, vals in enemy_values.items():
			without = max((v for c, v in vals.items() if c != civ), default=0.5)
			denial = max(denial, vals[civ] - without)
		for i in _open_slots(teams[team]):
			v, reasons = model.value(civ, teams[team][i], teams[enemy], draft.map_name, draft.map_category, left)
			options.append(dict(civ=civ, slot=i, seat=teams[team][i].seat, player=teams[team][i].name,
				value=round(v, 3), denial=round(max(0.0, denial), 3),
				score=round(v + DENIAL_WEIGHT * max(0.0, denial), 3), reasons=reasons))
	options.sort(key=lambda o: -o['score'])
	best, seen = [], set()
	for o in options:  # each civ once, for the slot it suits best
		if o['civ'] not in seen:
			seen.add(o['civ'])
			best.append(o)
	return best[:top]


def simulate(model: Model, draft: Draft):
	""" Play the rest of the draft with each team taking the top recommendation. Returns the picks made. """
	d = Draft(draft.map_name, draft.map_category, list(draft.available), draft.teams, list(draft.order), list(draft.picks),
		list(draft.bans))
	made = []
	while len(d.picks) < len(d.order):
		team = d.order[len(d.picks)]
		best = recommend(model, d, team, top=1)
		if not best:
			break
		d.picks.append((team, best[0]['slot'], best[0]['civ']))
		made.append(dict(team=team, **best[0]))
	return made, d


def ban(model: Model, draft: Draft, team: int, count=1):
	""" The civs `team` should ban: the ones the enemy would value most (best slot), counting what we'd lose too. """
	teams, left = _apply(draft)
	enemy = 1 - team
	scores = {}
	for civ in left:
		theirs = max(model.value(civ, s, teams[team], draft.map_name, draft.map_category, left)[0] for s in teams[enemy])
		scores[civ] = theirs
	return [c for c, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:count]]


def balance(model: Model, draft: Draft):
	"""
	How even a finished draft is: Elo win chance of team 0, the civ edge (average pick value of team 0 minus
	team 1, against the full enemy line-up) and both combined, plus each slot's final value.
	"""
	teams, _ = _apply(draft)
	elo_known = all(s.elo is not None for t in teams for s in t)
	elo_p = team_expectation([s.elo for s in teams[0]], [s.elo for s in teams[1]]) if elo_known else 0.5
	values = [[model.value(s.civ, s, teams[1 - t], draft.map_name, draft.map_category)[0] for s in teams[t]] for t in (0, 1)]
	edge = sum(values[0]) / len(values[0]) - sum(values[1]) / len(values[1])
	# A value edge is in win-chance points; add it on the log-odds scale so the total stays within 0..1.
	logit = math.log(elo_p / (1 - elo_p)) + 4 * edge
	return dict(elo=round(elo_p, 3), civ_edge=round(edge, 3), win=round(_logistic(logit), 3), values=values)


def snake_order(team_size, first=0):
	""" A-B-B-A-A-B-B-A... for team_size picks each. """
	order, t = [first], 1 - first
	while len(order) < 2 * team_size:
		order += [t, t]
		t = 1 - t
	return order[:2 * team_size]


def alternate_order(team_size, first=0):
	""" A-B-A-B... for team_size picks each. """
	return [(first + i) % 2 for i in range(2 * team_size)]


def fair_first_pick(teams):
	""" The team with the lower total Elo picks first. """
	totals = [sum(s.elo or 0 for s in t) for t in teams]
	return 0 if totals[0] < totals[1] else 1


def ranked_civs():
	return [c for c in techtree.civs() if c not in CHRONICLES]


# ---------------------------------------------------------------- /aoe draft

TEAM_CIVS, PLAYER_CIVS = 'team', 'player'  # civs per team (players sort them out) / a civ and seat for each player
EXCLUDE_MIN, EXCLUDE_MAX = 25, 35  # civs left out at random before every draft, so drafts don't repeat
SEAT_PRIOR = 4  # a player's win rate in a seat starts as 4 games at 50%
TEAM_CHOICES = 6  # civs each team gets to choose from in TEAM_CIVS mode (at least one per player)


def seat_layout(size, choices=None):
	"""
	Seats of a team from one end to the other: flanks at the ends, pockets between (none below 3 players).
	With choices (more civs than players), the extra civs are for flank, pocket, flank... in turn.
	"""
	seats = ['Flank'] + ['Pocket'] * (size - 2) + ['Flank'] if size >= 3 else [None] * size
	extra = max(0, (choices or size) - size)
	return seats + ([('Flank', 'Pocket')[i % 2] for i in range(extra)] if size >= 3 else [None] * extra)


def civ_pool(rng, civs=None, low=EXCLUDE_MIN, high=EXCLUDE_MAX, keep=0):
	""" (civs to draft from, civs left out): low..high ranked civs left out at random, at least `keep` kept. """
	civs = sorted(civs or ranked_civs())
	k = max(0, min(rng.randint(low, high), len(civs) - keep))
	out = set(rng.sample(civs, k))
	return [c for c in civs if c not in out], sorted(out)


def seat_record(record, seat):
	""" (win rate pulled towards 50%, games) of a player in a seat; record is one summaries.seat_stats() value. """
	st = (record or {}).get('seats', {}).get(seat)
	if not st or not st['games']:
		return 0.5, 0
	return (st['wins'] + SEAT_PRIOR * 0.5) / (st['decided'] + SEAT_PRIOR), st['games']


def assign_seats(team, records, rng):
	"""
	Seats for one team's players (dicts of name, elo, user_id): the split into flanks and pockets where the
	players' own records in those seats add up best; even splits are decided at random. -> Slots, end to end.
	"""
	layout = seat_layout(len(team))
	if len(team) < 3:
		return [Slot(seat, p.get('elo'), p.get('name'), user_id=p.get('user_id')) for seat, p in zip(layout, team)]
	best = None
	for flanks in combinations(range(len(team)), 2):
		score = sum(seat_record(records.get(p.get('user_id')), 'Flank' if i in flanks else 'Pocket')[0]
			for i, p in enumerate(team))
		key = (round(score, 6), rng.random())
		if best is None or key > best[0]:
			best = (key, flanks)
	flanks = [team[i] for i in best[1]]
	pockets = [p for i, p in enumerate(team) if i not in best[1]]
	return [Slot(seat, p.get('elo'), p.get('name'), user_id=p.get('user_id'))
		for seat, p in zip(layout, [flanks[0], *pockets, flanks[1]])]


def pick_order(sizes, first):
	""" Snake order (A-B-B-A-A-B...) for teams of these sizes; a full team is skipped. """
	order, counts, t, step = [], [0, 0], first, 0
	while len(order) < sum(sizes):
		if counts[t] < sizes[t]:
			order.append(t)
			counts[t] += 1
		step += 1
		if step % 2 == 1:  # A, then B B, then A A ...
			t = 1 - t
	return order


def civ_draft(model: Model, teams, map_name, category=None, mode=TEAM_CIVS, rng=None, records=None, civs=None):
	"""
	A whole draft for a started match. teams: two lists of dicts(name, elo, user_id). In TEAM_CIVS mode each team
	gets TEAM_CHOICES civs (team average Elo, no player) to choose its players' civs from; in PLAYER_CIVS mode every
	player gets a seat (assign_seats, from their flank/pocket records) and a civ. Both teams take the best civ
	left in turn (snake order, a coin flip for first pick) from the civs not left out at random. Elos still shape
	which civs suit each player (elo_fit, player_fit) but never which team gets the better civs.
	-> dict(mode, map, category, teams (Slots with civs), picks, excluded, first, balance)
	"""
	rng = rng or random.Random()
	picks = sum(len(t) if mode == PLAYER_CIVS else max(len(t), TEAM_CHOICES) for t in teams)
	pool, excluded = civ_pool(rng, civs, keep=picks + FIELD_SIZE)
	if mode == PLAYER_CIVS:
		slots = [assign_seats(t, records or {}, rng) for t in teams]
	else:
		slots = []
		for t in teams:
			elos = [p['elo'] for p in t if p.get('elo') is not None]
			avg = round(sum(elos) / len(elos)) if elos else None
			slots.append([Slot(seat, avg) for seat in seat_layout(len(t), max(len(t), TEAM_CHOICES))])
	# Elo is not evened out by civs: the rating change already does that (a favourite gains little and loses more).
	# So the snake draft only finds the strongest civs for the map and seats; they are then split between the teams
	# (same seats each side) so that both sides' civs are as even as possible.
	first = rng.randrange(2)
	d = Draft(map_name, category, pool, slots, pick_order([len(t) for t in slots], first))
	made, d = simulate(model, d)
	drafted, _ = _apply(d)
	final, _ = fair_split(model, drafted, map_name, category, rng, per_player=mode == PLAYER_CIVS)
	d = Draft(map_name, category, pool, final, [], [(t, i, s.civ) for t in (0, 1) for i, s in enumerate(final[t])])
	return dict(mode=mode, map=map_name, category=category, teams=final, picks=made, excluded=excluded, first=first,
		balance=balance(model, d), civ_count=len(civs or ranked_civs()))


EVEN_ENOUGH = 0.004  # splits this close to the most even one are all fine; one of them is picked at random


def fair_split(model: Model, teams, map_name, category=None, rng=None, per_player=False):
	"""
	Share the drafted civs between the two teams so that their civs are as even as possible: each team keeps its
	seats (as many flank and pocket civs as it has flank and pocket slots), every way of splitting the flank civs
	and the pocket civs is tried, and within a team each civ goes to the player it suits best (per_player).
	-> (teams of Slots with civs, civ edge of team 0)
	"""
	rng = rng or random.Random()
	seats = sorted({s.seat for t in teams for s in t}, key=str)
	civs_by_seat = {seat: [s.civ for t in teams for s in t if s.seat == seat] for seat in seats}
	slots_by_seat = [{seat: [i for i, s in enumerate(t) if s.seat == seat] for seat in seats} for t in teams]
	base, m_cache = {}, {}

	def own(t, i, civ):  # a civ's value in a slot, apart from the matchups
		k = (t, i, civ)
		if k not in base:
			slot = teams[t][i]
			strength = model.strength(civ, slot.seat, map_name, category)[0]
			fit = model.elo_fit(civ, slot.elo)[0]
			pfit = model.player_fit(slot, civ)[0] if per_player else 0.5
			base[k] = strength + ELO_FIT_WEIGHT * (fit - 0.5) + PLAYER_FIT_WEIGHT * (pfit - 0.5)
		return base[k]

	def vs(civ, seat, lineup):  # matchup part of a civ's value against an enemy line-up [(civ, seat)]
		total = weight = 0.0
		for ec, es in lineup:
			k = (civ, seat, ec, es)
			if k not in m_cache:
				m_cache[k] = model.matchup(civ, seat, ec, es, map_name, category)[0]
			face = SAME_SEAT_FACE if es == seat else CROSS_SEAT_FACE
			total += face * (m_cache[k] - 0.5)
			weight += face
		return total / weight if weight else 0.0

	def place(t, chosen):  # {seat: civs} -> {slot index: civ}, each civ with the player it suits best
		out = {}
		for seat, civs in chosen.items():
			idx = slots_by_seat[t][seat]
			order = civs
			if per_player and len(civs) <= 6:
				order = max(permutations(civs), key=lambda o: sum(own(t, i, c) for i, c in zip(idx, o)))
			out.update(zip(idx, order))
		return out

	splits = [[]]
	for seat in seats:
		k = len(slots_by_seat[0][seat])
		splits = [prev + [(seat, set(combo))] for prev in splits for combo in combinations(range(len(civs_by_seat[seat])), k)]
	options = []
	for split in splits:
		mine = [{seat: [civs_by_seat[seat][j] for j in sorted(js)] for seat, js in split},
			{seat: [c for j, c in enumerate(civs_by_seat[seat]) if j not in js] for seat, js in split}]
		placed = [place(0, mine[0]), place(1, mine[1])]
		lineups = [[(civ, teams[t][i].seat) for i, civ in placed[t].items()] for t in (0, 1)]
		values = [[own(t, i, civ) + MATCHUP_WEIGHT * vs(civ, teams[t][i].seat, lineups[1 - t])
			for i, civ in placed[t].items()] for t in (0, 1)]
		edge = sum(values[0]) / len(values[0]) - sum(values[1]) / len(values[1])
		options.append((abs(edge), edge, placed))
	best = min(o[0] for o in options)
	_, edge, placed = rng.choice([o for o in options if o[0] <= best + EVEN_ENOUGH])
	out = [[Slot(s.seat, s.elo, s.name, placed[t][i], s.user_id) for i, s in enumerate(teams[t])] for t in (0, 1)]
	return out, edge


STRONG_MATCHUP = 0.53  # a matchup worth naming in a pick's reason
CLOSED_STRONG = 0.6    # a part of the closed-map score worth naming in a pick's reason (and in the top quarter of civs)


def _top_quarter(key):
	""" The value of a closed-map score part that only the best quarter of ranked civs reach. """
	vals = sorted((fortress(c)[key] for c in ranked_civs()), reverse=True)
	return vals[len(vals) // 4]
GAMES_TO_TRUST = 3     # with fewer games of a civ, a reason says the pick is mostly the tech tree's guess


def pick_reason(model: Model, res, team, slot):
	""" One line on why a civ is in a civ_draft() result: what it is good at for the seat (on a closed map also its
	siege, gunpowder, towers or power units), how it did here against Elo, and its best matchup against the other
	team's civs. """
	s = res['teams'][team][slot]
	seat = s.seat.lower() if s.seat else None
	strength, n_map, n_all = model.strength(s.civ, s.seat, res['map'], res.get('category'))
	lines = main_lines(model, s.civ, s.seat)
	units = " and ".join(lines[:2])
	parts = []
	if n_all >= GAMES_TO_TRUST:
		diff = round(100 * (strength - 0.5))
		parts.append(f"{units}; {'even' if diff == 0 else f'{diff:+d}%'} against Elo in {n_all} games here" +
			(f" ({n_map} on this map)" if n_map else ""))
	else:
		parts.append(f"{units} {'suit a ' + seat if seat else 'are strong'} by its tech tree"
			+ (f" (only {n_all} game{'s' if n_all != 1 else ''} with it here)" if n_all else " (no games with it here yet)"))
	if CLOSED_LEAN.get(res.get('category')):
		f = fortress(s.civ)
		strong = [name for name, key in (('gunpowder', 'gunpowder'), ('siege', 'siege'), ('towers', 'towers'),
			('power units', 'power')) if f[key] >= max(CLOSED_STRONG, _top_quarter(key))]
		if strong:
			parts.append(f"strong {' and '.join(strong[:2])} for a {res['category'].lower()} map")
	if HYBRID_LEAN.get(res.get('category')):
		h = hybrid(s.civ, s.seat)
		good = []
		if h['water'] >= max(CLOSED_STRONG, _top_quarter_hybrid('water', s.seat)):
			good.append("strong navy and fishing")
		if HYBRID_BONUS.get(s.civ) == 'wood':
			good.append("a wood bonus")
		if h['cavalry'] >= max(CLOSED_STRONG, _top_quarter_hybrid('cavalry', s.seat)):
			good.append("cavalry a fish boom can pay for")
		if good:
			parts.append("on a hybrid map: " + ", ".join(good))
	best = None
	for e in res['teams'][1 - team]:
		m, _, _ = model.matchup(s.civ, s.seat, e.civ, e.seat, res['map'], res.get('category'))
		if best is None or m > best[0]:
			best = (m, e.civ)
	if best and best[0] >= STRONG_MATCHUP:
		parts.append(f"good against {best[1]} ({best[0]:.0%})")
	return "; ".join(parts)



def main_lines(model: Model, civ, seat, top=2):
	""" The civ's top fighting unit lines for the seat (siege, monks and heroes left out), for showing with a pick. """
	army = model.army(civ, seat)
	lines, seen = [], set()
	for k, _ in sorted(army.items(), key=lambda kv: -kv[1]):
		base = re.sub(r'^(Elite|Heavy|Imperial) ', '', k)  # "Heavy Hei Guang Cavalry" is the same line as "Hei Guang Cavalry"
		if base not in seen:
			seen.add(base)
			lines.append(k)
	hero = lambda k: sum(((techtree.unit(k) or {}).get('cost') or {}).values()) >= techtree.HERO_COST  # noqa: E731
	fighting = [k for k in lines if k not in SUPPORT_LINES and not hero(k)]
	return (fighting or lines)[:top]

