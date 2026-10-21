# -*- coding: utf-8 -*-
"""
Draft logic: which civ to pick next for a map, from the games the bot has analysed.

Nothing here is wired to Discord yet. It is meant to be used once enough games are saved; until then most
ratings stay close to "average" on purpose (every estimate is shrunk towards the prior by how few games
back it up). The prior is neutral now; community votes (the civ voting sheet) can be plugged in later
through `Prior`.

Pieces, all on a 0..1 scale where 0.5 is average:
- strength(civ, seat, map): wins and overall scores of that civ in that seat, on this exact map first, then
  the same map type, then any map.
- matchup(civ, seat, enemy civ, enemy seat, map): lost bases as head-to-head results (insights' aoe_matchups);
  a base taken by a civ whose team still lost the game counts more, since it shows the civ was stronger even
  though the team result went the other way.
- elo_fit(civ, elo): how the civ does in the player's Elo range compared with its overall results (low-rated
  players can't execute every civ or plan equally well).

recommend() ranks the possible next picks; simulate() plays a whole draft with both teams picking the best
available. Any format works: bans, pick order, seats and players are all inputs.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# How much each kind of evidence counts, and how many games it takes before evidence outweighs the prior.
MAP_WEIGHT, CATEGORY_WEIGHT, ANY_WEIGHT = 1.0, 0.5, 0.2
PRIOR_GAMES = 6          # strength: games worth of "average" every estimate starts with
MATCHUP_PRIOR = 4        # matchups: base fights worth of "even" every matchup starts with
LOSING_SIDE_BONUS = 1.5  # a base taken by a civ whose team lost the game counts 1.5x
ELO_FIT_PRIOR = 6
WIN_WEIGHT = 0.6         # performance of one game: 60% win/loss, 40% overall score (out of 10)
SAME_SEAT_FACE = 1.0     # how much a matchup against the enemy in the same kind of seat counts
CROSS_SEAT_FACE = 0.5    # ... and against the other kind of seat
MATCHUP_WEIGHT = 0.5     # matchups' share of a pick's value next to strength
ELO_FIT_WEIGHT = 0.3
DENIAL_WEIGHT = 0.5      # how much taking a civ away from the enemy is worth next to its value for us
ELO_RANGES = ((0, 1400), (1400, 1600), (1600, 1800), (1800, 99999))


def _norm(text):
	return (text or '').lower().replace(' ', '').replace('_', '')


def _game_perf(row):
	won = row.get('won')
	score = row.get('score_overall')
	parts, weight = 0.0, 0.0
	if won is not None:
		parts += WIN_WEIGHT * (1.0 if won else 0.0)
		weight += WIN_WEIGHT
	if score is not None:
		parts += (1 - WIN_WEIGHT) * max(0.0, min(1.0, score / 10))
		weight += 1 - WIN_WEIGHT
	return parts / weight if weight else None


def _shrunk(total, weight, prior=0.5, prior_games=PRIOR_GAMES):
	""" Weighted average pulled towards the prior by `prior_games` games of prior. """
	return (total + prior * prior_games) / (weight + prior_games)


class Prior:
	""" Starting rating for a civ in a seat on a map type, 0..1. Neutral now; the voting sheet can feed this later. """

	def __init__(self, table: Optional[Dict] = None):
		self.table = table or {}  # {(civ, seat, map_category): 0..1}

	def __call__(self, civ, seat, category):
		return self.table.get((civ, seat, category), 0.5)


@dataclass
class Slot:
	""" One player position in a team: seat ('Flank' / 'Pocket'), the player's BombayBot Elo, and their name. """
	seat: Optional[str]
	elo: Optional[int] = None
	name: Optional[str] = None
	civ: Optional[str] = None  # filled in once picked


@dataclass
class Draft:
	map_name: str
	map_category: Optional[str]
	available: List[str]                  # civs left after bans
	teams: List[List[Slot]]               # teams[0], teams[1]
	order: List[int] = field(default_factory=list)  # team index of each pick, e.g. [0, 1, 1, 0, 0, 1, 1, 0]
	picks: List[tuple] = field(default_factory=list)  # (team, slot index, civ) made so far


class Model:
	"""
	Ratings learned from saved games. rows: aoe_game_players rows (civ, seat, won, elo, score_overall, map_name,
	map_category); matchups: aoe_matchups rows.
	"""

	def __init__(self, rows, matchups=(), prior: Callable = None):
		self.prior = prior or Prior()
		self.rows = [r for r in rows if r.get('civ')]
		self.matchups = list(matchups)

	# ----- strength
	def strength(self, civ, seat, map_name, category=None):
		""" (rating 0..1, games behind it) for the civ in this seat on this map. """
		total = weight = 0.0
		games = 0
		for r in self.rows:
			if r['civ'] != civ or (seat and r.get('seat') != seat):
				continue
			perf = _game_perf(r)
			if perf is None:
				continue
			if _norm(r.get('map_name')) == _norm(map_name):
				w = MAP_WEIGHT
			elif category and r.get('map_category') == category:
				w = CATEGORY_WEIGHT
			else:
				w = ANY_WEIGHT
			total += w * perf
			weight += w
			games += 1
		return _shrunk(total, weight, self.prior(civ, seat, category)), games

	# ----- matchups from lost bases
	def matchup(self, civ, seat, enemy_civ, enemy_seat, map_name, category=None):
		""" (rating 0..1 for civ against enemy_civ, base fights behind it). 0.5 = even. """
		score = weight = 0.0
		fights = 0
		for m in self.matchups:
			if _norm(m.get('map_name')) == _norm(map_name):
				w = MAP_WEIGHT
			elif category and m.get('map_category') == category:
				w = CATEGORY_WEIGHT
			else:
				w = ANY_WEIGHT
			won_it = (m['winner_civ'], m.get('winner_seat'), m['loser_civ'], m.get('loser_seat')) == (civ, seat, enemy_civ, enemy_seat)
			lost_it = (m['winner_civ'], m.get('winner_seat'), m['loser_civ'], m.get('loser_seat')) == (enemy_civ, enemy_seat, civ, seat)
			if not (won_it or lost_it):
				continue
			if m.get('winner_team_won') is False:
				w *= LOSING_SIDE_BONUS  # took the base even though their team lost: a strong sign
			score += w * (1.0 if won_it else 0.0)
			weight += w
			fights += 1
		return _shrunk(score, weight, 0.5, MATCHUP_PRIOR), fights

	# ----- Elo fit
	def elo_fit(self, civ, elo):
		""" How the civ does for players in this Elo range compared with all its games (0.5 = no difference). """
		if elo is None:
			return 0.5, 0
		band = next(((lo, hi) for lo, hi in ELO_RANGES if lo <= elo < hi), None)
		perfs_all, perfs_band = [], []
		for r in self.rows:
			if r['civ'] != civ or r.get('elo') is None:
				continue
			perf = _game_perf(r)
			if perf is None:
				continue
			perfs_all.append(perf)
			if band and band[0] <= r['elo'] < band[1]:
				perfs_band.append(perf)
		if not perfs_band or not perfs_all:
			return 0.5, 0
		overall = sum(perfs_all) / len(perfs_all)
		diff = sum(p - overall for p in perfs_band)
		return 0.5 + diff / (len(perfs_band) + ELO_FIT_PRIOR), len(perfs_band)

	# ----- value of one pick
	def value(self, civ, slot: Slot, enemies: List[Slot], map_name, category=None):
		""" (value, reasons) of giving `civ` to `slot`, given the enemy civs picked so far. """
		strength, games = self.strength(civ, slot.seat, map_name, category)
		fit, fit_games = self.elo_fit(civ, slot.elo)
		vs, vs_weight, vs_notes = 0.0, 0.0, []
		for e in enemies:
			if not e.civ:
				continue
			face = SAME_SEAT_FACE if e.seat == slot.seat else CROSS_SEAT_FACE
			m, fights = self.matchup(civ, slot.seat, e.civ, e.seat, map_name, category)
			vs += face * (m - 0.5)
			vs_weight += face
			if fights:
				vs_notes.append(f"vs {e.civ} {e.seat or ''}: {m:.0%} from {fights} base fight{'s' if fights > 1 else ''}")
		matchup_part = vs / vs_weight if vs_weight else 0.0
		value = strength + MATCHUP_WEIGHT * matchup_part + ELO_FIT_WEIGHT * (fit - 0.5)
		reasons = [f"{strength:.0%} strength as {slot.seat or 'player'} from {games} game{'s' if games != 1 else ''}"]
		if fit_games:
			reasons.append(f"Elo fit {fit - 0.5:+.0%} from {fit_games} game{'s' if fit_games != 1 else ''} in their Elo range")
		reasons += vs_notes
		return value, reasons


def _open_slots(team):
	return [i for i, s in enumerate(team) if not s.civ]


def _apply(draft: Draft):
	teams = [[Slot(s.seat, s.elo, s.name, s.civ) for s in t] for t in draft.teams]
	for team, idx, civ in draft.picks:
		teams[team][idx].civ = civ
	taken = {civ for _, _, civ in draft.picks}
	return teams, [c for c in draft.available if c not in taken]


def recommend(model: Model, draft: Draft, team: int = None, top: int = 5):
	"""
	Ranked options for the next pick: dicts of civ, slot, value for us, denial (how much the best enemy slot
	would lose if we take it) and reasons. team defaults to whoever picks next in draft.order.
	"""
	teams, left = _apply(draft)
	if team is None:
		team = draft.order[len(draft.picks)] if len(draft.picks) < len(draft.order) else 0
	enemy = 1 - team
	options = []
	for civ in left:
		# Best value of this civ for the enemy, and their next best option without it: what taking it denies them.
		enemy_slots = _open_slots(teams[enemy])
		denial = 0.0
		if enemy_slots:
			for i in enemy_slots:
				v_with, _ = model.value(civ, teams[enemy][i], teams[team], draft.map_name, draft.map_category)
				v_without = max((model.value(c, teams[enemy][i], teams[team], draft.map_name, draft.map_category)[0]
					for c in left if c != civ), default=0.5)
				denial = max(denial, v_with - v_without)
		for i in _open_slots(teams[team]):
			v, reasons = model.value(civ, teams[team][i], teams[enemy], draft.map_name, draft.map_category)
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
	d = Draft(draft.map_name, draft.map_category, list(draft.available), draft.teams, list(draft.order), list(draft.picks))
	made = []
	while len(d.picks) < len(d.order):
		team = d.order[len(d.picks)]
		best = recommend(model, d, team, top=1)
		if not best:
			break
		pick = (team, best[0]['slot'], best[0]['civ'])
		d.picks.append(pick)
		made.append(dict(team=team, **best[0]))
	return made


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
