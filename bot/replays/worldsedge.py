# -*- coding: utf-8 -*-
"""
Small async client for the World's Edge (Relic Link) community API used by AoE2:DE, the same API the game
client, AoE2 Insights and AoE2 Companion use. It is not a documented public API, so every call is allowed
to fail: callers fall back to manual uploads.

Match history: /community/leaderboard/getRecentMatchHistory
Recorded games: the `matchurls` entries of a match (gzipped .aoe2record files), also reachable through the
official short link https://aoe.ms/replay/?gameId=<match id>&profileId=<profile id>.
"""
import json
import aiohttp

API = "https://aoe-api.worldsedgelink.com/community/leaderboard"
TIMEOUT = aiohttp.ClientTimeout(total=60)
MAX_REPLAY_BYTES = 40 * 1024 * 1024


class WorldsEdgeError(Exception):
	pass


# ---------- links shown in posts

def replay_link(match_id, profile_id):
	""" Official download link for the recorded game a player uploaded. """
	return f"https://aoe.ms/replay/?gameId={match_id}&profileId={profile_id}"


def match_links(match_id):
	return dict(
		insights=f"https://www.aoe2insights.com/match/{match_id}/",
		companion=f"https://www.aoe2companion.com/matches/{match_id}",
	)


# ---------- API

async def _get_json(session, path, **params):
	async with session.get(f"{API}/{path}", params=params) as r:
		if r.status != 200:
			raise WorldsEdgeError(f"World's Edge API returned HTTP {r.status} for {path}.")
		data = await r.json(content_type=None)
	if data.get('result', {}).get('code') != 0:
		raise WorldsEdgeError(f"World's Edge API error: {data.get('result')}")
	return data


async def recent_matches(profile_id):
	""" The player's recent matches (ranked and lobby games), newest first as returned by the API. """
	async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
		data = await _get_json(session, 'getRecentMatchHistory', title='age2', profile_ids=json.dumps([int(profile_id)]))
	return data.get('matchHistoryStats', [])


def match_profiles(match):
	return {r['profile_id'] for r in match.get('matchhistoryreportresults', [])} | \
		{r['profile_id'] for r in match.get('matchhistorymember', [])}


def match_meta(match):
	"""
	{profile_id: dict(won, team, civ_id)} from a match history entry.
	resulttype: 1 = win, 0 = loss (others = draw / dropped / unknown).
	"""
	out = {}
	for r in match.get('matchhistoryreportresults', []):
		out[r['profile_id']] = dict(
			won={1: True, 0: False}.get(r.get('resulttype')), team=r.get('teamid'), civ_id=r.get('civilization_id')
		)
	return out


def find_match(matches, profile_ids, reported_at, max_before=4 * 3600, max_after=15 * 60):
	"""
	The match that best fits a pickup game reported at `reported_at` (unix time): the most of our known
	players took part, and it finished closest to the report.
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


def find_by_start(matches, profile_ids, started_at, slack=15 * 60):
	"""
	The match a recorded game came from: same players, started within 15 minutes of the recording's start
	time (the recording and World's Edge clocks differ by up to a few minutes).
	"""
	profile_ids = set(profile_ids)
	best = None
	for m in matches:
		start = m.get('startgametime') or 0
		if not started_at or abs(start - started_at) > slack:
			continue
		overlap = len(profile_ids & match_profiles(m))
		if overlap * 2 < len(profile_ids):
			continue
		key = (overlap, -abs(start - started_at))
		if best is None or key > best[0]:
			best = (key, m)
	return best[1] if best else None


def replay_urls(match):
	""" {profile_id: url} for every player whose recorded game was uploaded. """
	return {u['profile_id']: u['url'] for u in match.get('matchurls', []) if u.get('url')}


def download_links(match, profile_id):
	""" Where to get a player's recording: the match's own link, then the official aoe.ms link (fresh redirect). """
	links = []
	if url := replay_urls(match).get(profile_id):
		links.append(url)
	links.append(replay_link(match['id'], profile_id))
	return links


async def download(url):
	async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
		async with session.get(url, allow_redirects=True) as r:
			if r.status != 200:
				body = (await r.text(errors='replace'))[:200].replace('\n', ' ')
				raise WorldsEdgeError(f"Recorded game download failed with HTTP {r.status} ({body.strip() or 'no details'}).")
			data = bytearray()
			async for chunk in r.content.iter_chunked(1 << 16):
				data += chunk
				if len(data) > MAX_REPLAY_BYTES:
					raise WorldsEdgeError("Recorded game file is too large.")
	return bytes(data)
