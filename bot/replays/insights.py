# -*- coding: utf-8 -*-
"""
Save what every analysed recorded game says about each player, so a future feature can
answer "who is good on which map, with which civ, in which seat, playing which strategy"
(for example to suggest civs and positions before a game).

Tables
  aoe_games         one row per analysed game
  aoe_game_players  one row per player per game (primary key game_key + profile_id)

Re-analysing the same game replaces its rows, so uploading a recorded game twice never
double counts. Bump ANALYSIS_VERSION when scoring changes, so old rows can be told apart.
"""
import hashlib
import re
import time

from core.console import log
from core.database import db

ANALYSIS_VERSION = 4

# Map categories used by the group's civ voting sheet; anything unknown stays "Other".
MAP_CATEGORIES = [
	('Semi Closed', ('palisade arena', 'hideout', 'oasis')),
	('Closed', ('arena', 'black forest', 'fortress')),
	('Hybrid', ('four lakes', 'kawasan', 'golden lakes')),
	('Nomad', ('land nomad', 'nomad', 'african clearing', 'bedouin')),
	('Open', ('arabia', 'golden pit', 'valley', 'grand bara')),
]

db.ensure_table(dict(
	tname="aoe_games",
	columns=[
		dict(cname="game_key", ctype=db.types.str),
		dict(cname="fingerprint", ctype=db.types.str),
		dict(cname="worldsedge_id", ctype=db.types.int),
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="map_name", ctype=db.types.str),
		dict(cname="map_category", ctype=db.types.str),
		dict(cname="lobby", ctype=db.types.str),
		dict(cname="team_size", ctype=db.types.int),
		dict(cname="duration_s", ctype=db.types.int),
		dict(cname="source", ctype=db.types.str),
		dict(cname="uploaded_by", ctype=db.types.int),
		dict(cname="rec_profile_id", ctype=db.types.int),
		dict(cname="analysis_version", ctype=db.types.int),
		dict(cname="at", ctype=db.types.int)
	],
	primary_keys=["game_key"]
))

db.ensure_table(dict(
	tname="aoe_game_players",
	columns=[
		dict(cname="game_key", ctype=db.types.str),
		dict(cname="profile_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="name", ctype=db.types.str),
		dict(cname="map_name", ctype=db.types.str),
		dict(cname="map_category", ctype=db.types.str),
		dict(cname="team_size", ctype=db.types.int),
		dict(cname="civ", ctype=db.types.str),
		dict(cname="civ_id", ctype=db.types.int),
		dict(cname="team", ctype=db.types.int),
		dict(cname="seat", ctype=db.types.str),
		dict(cname="won", ctype=db.types.bool),
		dict(cname="elo", ctype=db.types.int),
		dict(cname="tg_rating", ctype=db.types.int),
		dict(cname="strategy", ctype=db.types.str),
		dict(cname="opening", ctype=db.types.str),
		dict(cname="boom", ctype=db.types.bool),
		dict(cname="main_units", ctype=db.types.str),
		dict(cname="feudal_units", ctype=db.types.str),
		dict(cname="feudal_attack", ctype=db.types.bool),
		dict(cname="forward_towers", ctype=db.types.int),
		dict(cname="castle_units", ctype=db.types.str),
		dict(cname="imperial_units", ctype=db.types.str),
		dict(cname="fast_imp", ctype=db.types.bool),
		dict(cname="forward_castles", ctype=db.types.int),
		dict(cname="tower_creep", ctype=db.types.bool),
		dict(cname="feudal_s", ctype=db.types.int),
		dict(cname="castle_s", ctype=db.types.int),
		dict(cname="imperial_s", ctype=db.types.int),
		dict(cname="apm", ctype=db.types.int),
		dict(cname="villagers", ctype=db.types.int),
		dict(cname="score_exec", ctype=db.types.float),
		dict(cname="score_pressure", ctype=db.types.float),
		dict(cname="score_survival", ctype=db.types.float),
		dict(cname="score_team", ctype=db.types.float),
		dict(cname="score_overall", ctype=db.types.float),
		dict(cname="score_rank", ctype=db.types.int),
		dict(cname="elo_delta", ctype=db.types.float),
		dict(cname="elo_verdict", ctype=db.types.str),
		dict(cname="pressure_dealt", ctype=db.types.int),
		dict(cname="army_avg_units", ctype=db.types.int),
		dict(cname="army_peak_units", ctype=db.types.int),
		dict(cname="army_value_on_map", ctype=db.types.int),
		dict(cname="army_unit_value", ctype=db.types.int),
		dict(cname="army_value_sent", ctype=db.types.int),
		dict(cname="pressure_taken", ctype=db.types.int),
		dict(cname="base_note", ctype=db.types.text),
		dict(cname="tributes_sent", ctype=db.types.int),
		dict(cname="tributes_received", ctype=db.types.int),
		dict(cname="analysis_version", ctype=db.types.int),
		dict(cname="at", ctype=db.types.int)
	],
	primary_keys=["game_key", "profile_id"]
))


def map_category(map_name):
	name = (map_name or '').lower()
	for category, keys in MAP_CATEGORIES:
		if any(k in name for k in keys):
			return category
	return 'Other'


def fingerprint(rec):
	"""
	Same value for every player's recording of one game: start time plus who played which civ on which team.
	(Recordings differ byte for byte between players, so a file hash can't spot the same game.)
	"""
	players = sorted(f"{p['profile_id']}:{p['civ_id']}:{p['team_id']}" for p in rec['players'])
	raw = f"{rec.get('started_at') or ''}|{rec.get('map_name') or ''}|" + ",".join(players)
	return "fp:" + hashlib.sha1(raw.encode()).hexdigest()[:20]


def game_key(worldsedge_id=None, filename=None, rec=None):
	""" World's Edge id when known (also found in names like AgeIIDE_Replay_514937603), else the fingerprint. """
	if worldsedge_id:
		return f"we:{worldsedge_id}"
	if filename and (m := re.search(r"Replay_(\d{6,})", filename)):
		return f"we:{m.group(1)}"
	return fingerprint(rec)


def _secs(ms):
	return int(ms // 1000) if ms is not None else None


async def save(result, key, map_name=None, lobby=None, match_id=None, channel_id=None, source='upload',
		uploaded_by=None, rec_profile_id=None, fingerprint=None):
	"""
	Store the game and one row per player, and return the key it was stored under (None on failure).
	A game already stored under another key (same fingerprint) is updated in place, never counted twice;
	a World's Edge id wins over a fingerprint-only key. Never raises: stats must not break posting.
	"""
	try:
		if fingerprint:
			other = await db.select_one(('game_key',), 'aoe_games', where=dict(fingerprint=fingerprint))
			if other and other['game_key'] != key:
				if key.startswith('we:') and not other['game_key'].startswith('we:'):
					await db.delete('aoe_game_players', where=dict(game_key=other['game_key']))
					await db.delete('aoe_games', where=dict(game_key=other['game_key']))
				else:
					key = other['game_key']
		players, stats = result['players'], result['stats']
		teams = {}
		for p in players.values():
			teams[p['team_id']] = teams.get(p['team_id'], 0) + 1
		team_size = max(teams.values()) if teams else None
		category = map_category(map_name)
		now = int(time.time())
		we_id = int(key[3:]) if key.startswith('we:') else None
		# Keep the pickup match link if this game was already saved from a report.
		if old := await db.select_one(('match_id', 'channel_id'), 'aoe_games', where=dict(game_key=key)):
			match_id = match_id or old.get('match_id')
			channel_id = channel_id or old.get('channel_id')

		await db.insert('aoe_games', dict(
			game_key=key, fingerprint=fingerprint, worldsedge_id=we_id, match_id=match_id, channel_id=channel_id, map_name=map_name,
			map_category=category, lobby=lobby, team_size=team_size, duration_s=_secs(result['duration']),
			source=source, uploaded_by=uploaded_by, rec_profile_id=rec_profile_id,
			analysis_version=ANALYSIS_VERSION, at=now
		), on_dublicate="replace")

		rows = []
		for pid, p in players.items():
			s = stats[pid]
			sc = s['scores']
			parts = s.get('strategy_parts', {})
			profile_id = p.get('profile_id') or 0
			rows.append(dict(
				game_key=key, profile_id=profile_id if profile_id else -pid,  # bots/AI get a negative slot id
				user_id=p.get('user_id'), name=p['name'], map_name=map_name, map_category=category,
				team_size=team_size, civ=p['civ'], civ_id=p['civ_id'], team=p['team_id'], seat=p['seat'],
				won=p['won'], elo=p.get('elo'), tg_rating=p.get('rating'),
				strategy=s['strategy'], opening=parts.get('opening'), boom=parts.get('boom'),
				main_units=", ".join(parts.get('main_units', [])), fast_imp=parts.get('fast_imp'),
				feudal_units=", ".join(parts.get('feudal_units', [])), feudal_attack=parts.get('feudal_attack'),
				forward_towers=parts.get('forward_towers'), castle_units=", ".join(parts.get('castle_units', [])),
				imperial_units=", ".join(parts.get('imperial_units', [])),
				forward_castles=parts.get('forward_castles'), tower_creep=parts.get('tower_creep'),
				feudal_s=_secs(s['ages'].get('Feudal Age')), castle_s=_secs(s['ages'].get('Castle Age')),
				imperial_s=_secs(s['ages'].get('Imperial Age')), apm=s['apm'], villagers=s['villagers'],
				score_exec=sc['exec'], score_pressure=sc['press'], score_survival=sc['surv'], score_team=sc['team'],
				score_overall=sc['overall'], score_rank=s.get('score_rank'), elo_delta=s.get('elo_delta'),
				elo_verdict=s.get('elo_verdict'), pressure_dealt=s.get('press_out'),
				army_avg_units=s['army']['avg_units'], army_peak_units=s['army']['peak_units'],
				army_value_on_map=s['army']['avg_value_on_map'], army_unit_value=s['army']['unit_value'],
				army_value_sent=s['army']['value_sent'], pressure_taken=s.get('pressure_in'),
				base_note=(s.get('survival_note') or '')[:2000], tributes_sent=s.get('trib_out'),
				tributes_received=s.get('trib_in'), analysis_version=ANALYSIS_VERSION, at=now
			))
		await db.delete('aoe_game_players', where=dict(game_key=key))
		await db.insert_many('aoe_game_players', rows)
		log.info(f"Saved replay insights for {key}: {len(rows)} players on {map_name} ({category}).")
		return key
	except Exception as e:
		log.error(f"Could not save replay insights for {key}: {e}")
		return None
