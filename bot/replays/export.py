# -*- coding: utf-8 -*-
"""
/aoe export: the analysed games of a queue channel as one JSON file of "game cards", ready to hand to an AI
(e.g. for drafting civs by map, position and Elo). Each card joins a game's players, lost bases, fights and
head-to-head results of direct opponents. Pure functions over rows except load_cards().
"""
import gzip
import json
import time
from collections import defaultdict

from core.database import db

MAX_PLAIN_BYTES = 8 * 1024 * 1024  # bigger than this is gzipped so it still fits a Discord upload

GUIDE = {
	"about": "BombayBot analysed AoE2 DE team games (one card per game), from the players' recorded games.",
	"scores": "1-10 within each game (10 = best in that lobby; army sent: share of the game's best). overall = "
		"execution, pressure, holding position (survival) and team play weighted by seat (flank 27/36/27/10, "
		"pocket 40/25/25/10).",
	"elo": "BombayBot Elo when the match was made. elo_delta = overall score minus the score expected from Elo "
		"(spread-based, team rank, 100-Elo tiers); +1.5 above, -1.5 below their Elo.",
	"seat": "Flank or Pocket. In 4v4 each flank faces the enemy flank on its side and each pocket the enemy pocket.",
	"times": "Seconds from the start of the game.",
	"lanes": "Head-to-head of direct opponents: army value and villagers each killed of the other, bases lost; "
		"edge > 0 means player a came out ahead (army value, 100 per villager, 3000 for a lost base).",
	"fights": "Major fights between two players: units each side queued in the 5 minutes before and during the "
		"fight, army value each killed, a_share = a's share of value killed, expected = share predicted by Elo.",
	"matchups": "Lost bases: which civ and seat took which civ and seat's base.",
	"limits": "Read from player commands, not kills: kills by patrols, towers and castles are not seen.",
}

PLAYER_FIELDS = (
	'name', 'user_id', 'profile_id', 'civ', 'seat', 'won', 'elo', 'elo_delta', 'elo_verdict', 'tg_rating',
	'strategy', 'opening', 'boom', 'fish_boom', 'feudal_boom', 'feudal_units', 'castle_units', 'imperial_units',
	'feudal_attack', 'fast_imp', 'forward_castles', 'forward_towers', 'tower_creep',
	'feudal_s', 'castle_s', 'imperial_s', 'apm', 'villagers_made', 'villagers_lost', 'villagers_peak',
	'villagers_killed', 'fishing_ships_made', 'trade_carts', 'avg_pop', 'spend_per_min', 'float_minutes',
	'score_overall', 'score_exec', 'score_pressure', 'score_survival', 'score_team', 'score_rank',
	'playstyle', 'macro_score', 'micro_score',
	'value_killed', 'value_lost', 'army_value_on_map', 'army_value_sent', 'army_peak_units',
	'fight_vs_elo', 'several_bonus', 'forward_value', 'forward_effect', 'forward_bonus', 'age_delay_bonus',
	'macro_disrupted', 'buildings_destroyed', 'tributes_sent', 'tributes_received', 'base_note',
)


BOOL_FIELDS = {'won', 'boom', 'fish_boom', 'feudal_boom', 'feudal_attack', 'fast_imp', 'tower_creep', 'a_won', 'b_won',
	'a_base_lost', 'b_base_lost', 'winner_team_won'}


def _clean(d):
	""" Drop empty values so cards stay small; yes/no columns (1/0 in MySQL) become true/false. """
	return {k: (bool(v) if k in BOOL_FIELDS else v) for k, v in d.items() if v not in (None, '', [], {})}


def _json(text):
	try:
		return json.loads(text) if isinstance(text, str) else text
	except ValueError:
		return text


def _norm(text):
	return (text or '').lower().replace(' ', '').replace('_', '')


def build_cards(games, players, matchups=(), fights=(), lanes=(), details=False):
	""" One card per game, newest first. Rows are dicts as stored in the aoe_* tables. """
	by_game = defaultdict(lambda: dict(players=[], matchups=[], fights=[], lanes=[]))
	for r in players:
		p = _clean({k: r.get(k) for k in PLAYER_FIELDS})
		if details and r.get('explain_json'):
			p['explanation'] = _json(r['explain_json'])
		by_game[r['game_key']]['players'].append((r.get('team'), p))
	for r in matchups:
		by_game[r['game_key']]['matchups'].append(_clean({k: r.get(k) for k in (
			'winner_name', 'winner_civ', 'winner_seat', 'winner_elo', 'loser_name', 'loser_civ', 'loser_seat',
			'loser_elo', 'share', 'winner_team_won')}))
	for r in fights:
		f = {k: r.get(k) for k in ('start_s', 'a_name', 'a_civ', 'a_seat', 'a_elo', 'a_side', 'a_killed',
			'b_name', 'b_civ', 'b_seat', 'b_elo', 'b_side', 'b_killed', 'a_share', 'expected')}
		f['a_units'], f['b_units'] = _json(r.get('a_lines')), _json(r.get('b_lines'))
		by_game[r['game_key']]['fights'].append(_clean(f))
	for r in lanes:
		by_game[r['game_key']]['lanes'].append(_clean({k: r.get(k) for k in (
			'seat', 'a_name', 'a_civ', 'a_seat', 'a_elo', 'a_won', 'b_name', 'b_civ', 'b_seat', 'b_elo', 'b_won',
			'a_army_killed', 'b_army_killed', 'a_villagers_killed', 'b_villagers_killed', 'a_base_lost',
			'b_base_lost', 'a_castle_s', 'b_castle_s', 'a_overall', 'b_overall', 'edge')}))
	cards = []
	for g in sorted(games, key=lambda g: -(g.get('at') or 0)):
		parts = by_game.get(g['game_key'])
		if not parts or not parts['players']:
			continue
		teams = defaultdict(list)
		for team, p in parts['players']:
			teams[team].append(p)
		card = _clean(dict(
			game=g['game_key'], worldsedge_id=g.get('worldsedge_id'), match_id=g.get('match_id'),
			date=time.strftime('%Y-%m-%d', time.gmtime(g['at'])) if g.get('at') else None,
			map=g.get('map_name'), map_type=g.get('map_category'), team_size=g.get('team_size'),
			duration_s=g.get('duration_s'), lobby=g.get('lobby'), analysis_version=g.get('analysis_version'),
		))
		card['teams'] = [dict(won=next((p.get('won') for p in ps if p.get('won') is not None), None),
			players=sorted(ps, key=lambda p: (p.get('seat') or '', -(p.get('elo') or 0)))) for _, ps in sorted(teams.items(),
			key=lambda kv: str(kv[0]))]
		for k in ('lanes', 'matchups', 'fights'):
			if parts[k]:
				card[k] = parts[k]
		cards.append(card)
	return cards


def filter_games(games, map_filter=None, days=None, now=None):
	now = now or time.time()
	out = []
	for g in games:
		if map_filter and _norm(map_filter) not in _norm(g.get('map_name')) and _norm(map_filter) not in _norm(g.get('map_category')):
			continue
		if days and (g.get('at') or 0) < now - days * 86400:
			continue
		out.append(g)
	return out


async def load_cards(channel_id, map_filter=None, days=None, details=False):
	games = filter_games(await db.select(('*',), 'aoe_games', where=dict(channel_id=channel_id)), map_filter, days)
	keys = {g['game_key'] for g in games}
	if not keys:
		return []

	async def rows(table):
		return [r for r in await db.select(('*',), table) if r.get('game_key') in keys]

	return build_cards(games, await rows('aoe_game_players'), await rows('aoe_matchups'), await rows('aoe_fights'),
		await rows('aoe_lanes'), details)


def export_file(cards, channel_name=None, filters=None):
	""" (bytes, filename) of one JSON file: the guide and every card; gzipped if too big for a plain upload. """
	doc = dict(exported_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), channel=channel_name,
		filters=_clean(filters or {}), games=len(cards), guide=GUIDE, cards=cards)
	data = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
	name = f"bombaybot_games_{time.strftime('%Y%m%d', time.gmtime())}.json"
	if len(data) > MAX_PLAIN_BYTES:
		return gzip.compress(data), name + '.gz'
	return data, name
