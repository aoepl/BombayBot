# -*- coding: utf-8 -*-
"""
Small async client for the World's Edge (Relic Link) community API used by AoE2:DE.
Match history: /community/leaderboard/getRecentMatchHistory
Player stats:  /community/leaderboard/GetPersonalStat
Replays are served from the `matchurls` entries of a match (gzipped .aoe2record files).
"""
import json
import re
import aiohttp

API = "https://aoe-api.worldsedgelink.com/community/leaderboard"
TG_RM_LEADERBOARD = 4   # Team Random Map
RM_LEADERBOARD = 3      # 1v1 Random Map
TIMEOUT = aiohttp.ClientTimeout(total=60)
MAX_REPLAY_BYTES = 40 * 1024 * 1024


class WorldsEdgeError(Exception):
	pass


async def _get_json(session, path, **params):
	async with session.get(f"{API}/{path}", params=params) as r:
		if r.status != 200:
			raise WorldsEdgeError(f"World's Edge API returned HTTP {r.status} for {path}.")
		data = await r.json(content_type=None)
	if data.get('result', {}).get('code') != 0:
		raise WorldsEdgeError(f"World's Edge API error: {data.get('result')}")
	return data


def parse_identifier(text: str):
	"""
	Accepts a profile id ("664715"), a Steam id ("76561198..."), or a link from
	aoe2insights / aoe2companion / ageofempires.com that contains the profile id.
	Returns ('profile', int) or ('steam', str).
	"""
	text = text.strip()
	if re.fullmatch(r"7656\d{13}", text):
		return 'steam', text
	if re.fullmatch(r"\d{1,10}", text):
		return 'profile', int(text)
	for pattern in (r"aoe2insights\.com/user/(\d+)", r"aoe2companion\.com/profile/(\d+)",
			r"profile_?[iI]d=(\d+)", r"steamcommunity\.com/profiles/(7656\d{13})"):
		if m := re.search(pattern, text):
			value = m.group(1)
			return ('steam', value) if value.startswith('7656') and len(value) == 17 else ('profile', int(value))
	raise WorldsEdgeError("Give a profile id, a Steam id, or a link to your AoE2 Insights / AoE2 Companion profile.")


async def lookup_profiles(profile_ids=None, steam_ids=None):
	"""
	Returns {profile_id: dict(alias, steam_id, country, tg_rating, rm_rating)}.
	"""
	params = dict(title='age2')
	if profile_ids:
		params['profile_ids'] = json.dumps([int(p) for p in profile_ids])
	if steam_ids:
		params['profile_names'] = json.dumps([f"/steam/{s}" for s in steam_ids])
	async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
		data = await _get_json(session, 'GetPersonalStat', **params)

	group_to_profile = {}
	out = {}
	for group in data.get('statGroups', []):
		for m in group.get('members', []):
			pid = m['profile_id']
			name = m.get('name') or ''
			out[pid] = dict(
				alias=m.get('alias'), steam_id=name.split('/')[-1] if name.startswith('/steam/') else None,
				country=m.get('country'), tg_rating=None, rm_rating=None
			)
			if m.get('personal_statgroup_id'):
				group_to_profile[m['personal_statgroup_id']] = pid
	for s in data.get('leaderboardStats', []):
		pid = group_to_profile.get(s.get('statgroup_id'))
		if pid is None:
			continue
		if s.get('leaderboard_id') == TG_RM_LEADERBOARD:
			out[pid]['tg_rating'] = s.get('rating')
		elif s.get('leaderboard_id') == RM_LEADERBOARD:
			out[pid]['rm_rating'] = s.get('rating')
	return out


async def recent_matches(profile_id):
	async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
		data = await _get_json(session, 'getRecentMatchHistory', title='age2', profile_ids=json.dumps([int(profile_id)]))
	return data.get('matchHistoryStats', [])


def match_profiles(match):
	return {r['profile_id'] for r in match.get('matchhistoryreportresults', [])} | \
		{r['profile_id'] for r in match.get('matchhistorymember', [])}


def match_meta(match):
	"""
	{profile_id: dict(won, team, rating)} from a match history entry.
	resulttype: 1 = win, 0 = loss (others = draw / dropped / unknown).
	"""
	out = {}
	for r in match.get('matchhistoryreportresults', []):
		out[r['profile_id']] = dict(
			won={1: True, 0: False}.get(r.get('resulttype')), team=r.get('teamid'), civ_id=r.get('civilization_id')
		)
	for m in match.get('matchhistorymember', []):
		d = out.setdefault(m['profile_id'], {})
		if m.get('oldrating'):
			d['rating'] = m.get('oldrating')
	return out


def find_match(matches, profile_ids, reported_at, max_before=4 * 3600, max_after=15 * 60):
	"""
	Pick the match that best fits a pickup game reported at `reported_at` (unix time):
	the most of our linked players took part, and it finished closest to the report.
	"""
	profile_ids = set(profile_ids)
	need = 1 if len(profile_ids) == 1 else max(2, (len(profile_ids) + 1) // 2)
	best = None
	for m in matches:
		end = m.get('completiontime') or m.get('startgametime') or 0
		if not (reported_at - max_before <= end <= reported_at + max_after):
			continue
		overlap = len(profile_ids & match_profiles(m))
		if overlap < need:
			continue
		key = (overlap, -abs(reported_at - end))
		if best is None or key > best[0]:
			best = (key, m)
	return best[1] if best else None


def replay_urls(match):
	""" {profile_id: url} for every player that uploaded a recorded game. """
	return {u['profile_id']: u['url'] for u in match.get('matchurls', []) if u.get('url')}


async def download(url):
	async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
		async with session.get(url) as r:
			if r.status != 200:
				raise WorldsEdgeError(f"Replay download failed with HTTP {r.status}.")
			data = bytearray()
			async for chunk in r.content.iter_chunked(1 << 16):
				data += chunk
				if len(data) > MAX_REPLAY_BYTES:
					raise WorldsEdgeError("Replay file is too large.")
	return bytes(data)
