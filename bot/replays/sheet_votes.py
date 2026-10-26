# -*- coding: utf-8 -*-
"""
The players' average votes from the civ voting sheet (AoE2_Civ_Map_Votes), for drafts.

The sheet is readable by anyone with the link, so its tabs are fetched as CSV (no Google account needed) and kept
for CACHE_SECONDS. If the sheet can't be reached, the snapshot in player_votes.json is used. Claude's own column is
not part of these averages (the sheet leaves it out of its "Avg" columns).

-> {map type: {civ: {'Flank': (average, votes), 'Pocket': (average, votes)}}}; Nomad has one seat, 'any'.
"""
import csv
import io
import json
import logging
import os
import time

import aiohttp

SHEET_ID = '1tdPdm5WvUeArmrx4rvSU9neUdRPb4KyFvJFyYMWyzt0'
TABS = {'Open': 1076363915, 'Closed': 1586226038, 'Semi Closed': 1586226100, 'Hybrid': 1930743957, 'Nomad': 1250399439}
URL = 'https://docs.google.com/spreadsheets/d/{sheet}/export?format=csv&gid={gid}'
CACHE_SECONDS = 6 * 3600
TIMEOUT = aiohttp.ClientTimeout(total=20)

_cache = {'at': 0.0, 'votes': None}
log = logging.getLogger(__name__)


def _number(text):
	try:
		return float(str(text).strip())
	except ValueError:
		return None


def parse(text, nomad=False):
	""" One tab's CSV -> {civ: {seat: (average, votes)}}. Finds the header row by its 'Avg' columns. """
	rows = list(csv.reader(io.StringIO(text)))
	head = None
	for i, r in enumerate(rows):
		cells = [c.strip() for c in r]
		if ('Avg' in cells and 'Votes' in cells) if nomad else ('Flank Avg' in cells and 'Pocket Avg' in cells):
			head = i
			break
	if head is None:
		return {}
	cells = [c.strip() for c in rows[head]]
	if nomad:
		cols = {'any': (cells.index('Avg'), cells.index('Votes'))}
	else:
		cols = {'Flank': (cells.index('Flank Avg'), cells.index('Flank Votes')),
			'Pocket': (cells.index('Pocket Avg'), cells.index('Pocket Votes'))}
	out = {}
	for r in rows[head + 1:]:
		civ = r[0].strip() if r else ''
		if not civ or civ == 'Civilization':
			continue
		seats = {}
		for seat, (a, n) in cols.items():
			avg = _number(r[a]) if a < len(r) else None
			votes = _number(r[n]) if n < len(r) else None
			if avg is not None and votes:
				seats[seat] = (avg, int(votes))
		if seats:
			out[civ] = seats
	return out


def snapshot():
	try:
		with open(os.path.join(os.path.dirname(__file__), 'player_votes.json'), encoding='utf-8') as f:
			raw = json.load(f).get('votes', {})
	except (OSError, ValueError):
		return {}
	return {mt: {civ: {s: (v[0], v[1]) for s, v in seats.items()} for civ, seats in civs.items()} for mt, civs in raw.items()}


async def player_votes():
	""" The players' average votes, from the sheet (cached) or the snapshot. """
	if _cache['votes'] is not None and time.time() - _cache['at'] < CACHE_SECONDS:
		return _cache['votes']
	votes = {}
	try:
		async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
			for tab, gid in TABS.items():
				async with session.get(URL.format(sheet=SHEET_ID, gid=gid)) as r:
					if r.status != 200:
						raise RuntimeError(f"{tab}: HTTP {r.status}")
					votes[tab] = parse(await r.text(), nomad=tab == 'Nomad')
	except Exception as e:  # network, Google or a changed layout: fall back to the snapshot
		log.warning("civ voting sheet not read (%s); using the snapshot", e)
		votes = snapshot()
	if not any(votes.values()):
		votes = snapshot()
	_cache.update(at=time.time(), votes=votes)
	return votes
