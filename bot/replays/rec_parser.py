# -*- coding: utf-8 -*-
"""
Read AoE2:DE recorded games (.aoe2record) into plain python data.

Uses the `mgz` package for decompression and the action/sync stream, and reads the
DE player block itself because newer game versions (save 68+) add a field per
player that older mgz releases do not know about.
"""
import io
import gzip
import zipfile
import struct
import json
import re
from functools import lru_cache
from importlib import resources

# mgz is imported inside the functions, so the rest of the bot still starts if it is missing.

# Action types we keep; everything else is dropped to save memory.
KEEP_ACTIONS = {
	'MOVE', 'ORDER', 'DE_ATTACK_MOVE', 'PATROL', 'ATTACK_GROUND', 'BUILD', 'WALL', 'REPAIR', 'RESEARCH',
	'DE_QUEUE', 'QUEUE', 'MULTIQUEUE', 'DELETE', 'RESIGN', 'DE_TRIBUTE', 'FLARE', 'BUY', 'SELL',
	'GATHER_POINT', 'DE_MULTI_GATHERPOINT', 'UNGARRISON', 'SPECIAL', 'FORMATION', 'STANCE', 'FOLLOW', 'GUARD'
}


class RecError(Exception):
	pass


def unwrap(data: bytes) -> bytes:
	""" Accept a raw .aoe2record, a .zip containing one, or a gzip blob (as served by World's Edge). """
	for _ in range(3):
		if data[:2] == b'\x1f\x8b':
			data = gzip.decompress(data)
		elif data[:4] == b'PK\x03\x04':
			with zipfile.ZipFile(io.BytesIO(data)) as z:
				names = [n for n in z.namelist() if n.lower().endswith('.aoe2record')] or z.namelist()
				if not names:
					raise RecError("Zip file has no recorded game inside.")
				data = z.read(names[0])
		else:
			return data
	return data


@lru_cache(maxsize=1)
def reference():
	""" Object/tech/civ names from the aocref dataset shipped with mgz. """
	path = resources.files('aocref').joinpath('data/datasets/100.json')
	return json.loads(path.read_text(encoding='utf-8'))


def _de_string(data):
	if data.read(2) != b'\x60\x0a':
		raise RecError("unexpected string marker")
	length, = struct.unpack('<h', data.read(2))
	return data.read(length)


def _read_players(header, save, extra_string):
	""" Mirror of mgz.fast.header.parse_de up to the player block, with an optional extra per-player string. """
	u = lambda fmt: struct.unpack(fmt, header.read(struct.calcsize(fmt)))  # noqa: E731
	started_at = None
	if save >= 25.22:
		u('<I')
	if save >= 26.16:
		started_at, = u('<I')  # unix time the game started
	header.read(12)
	for _ in range(u('<I')[0]):
		u('<I')
	header.read(4)
	u('<I')
	header.read(4)
	rms_map_id, = u('<I')
	header.read(4)
	u('<I'); u('<I'); u('<I'); u('<I')
	header.read(12)
	u('<f')
	u('<I'); u('<I')
	num_players, = u('<I')
	header.read(14)
	if save >= 61.5:
		u('<B')
	u('<bb')
	header.read(1)
	u('<b'); u('<b'); u('<b'); u('<b'); u('<b'); u('<b'); u('<b'); u('<b'); u('<b'); u('<b')
	header.read(12)
	if save >= 25.06:
		header.read(1)
	if save > 50:
		header.read(1)
	players = []
	for _ in range(num_players if 66.3 > save >= 37 else 8):
		header.read(4)
		color_id, = u('<i')
		header.read(2)
		team_id, = u('<b')
		header.read(9)
		civ_id, = u('<I')
		if save >= 61.5:
			count, = u('<I')
			if save >= 63.0 and count > 0:
				for _ in range(count):
					u('<I')
		_de_string(header)
		header.read(1)
		_de_string(header)  # ai name
		if save >= 66.3:
			_de_string(header)  # censored name
		name = _de_string(header).decode('utf-8', 'replace')
		ptype, = u('<I')
		profile_id, number = u('<I4xi')
		if save < 25.22:
			header.read(8)
		u('b')
		header.read(1)
		if save >= 25.06:
			header.read(8)
		if save >= 64.3:
			header.read(4)
		if extra_string:
			_de_string(header)
		if number >= 1 and name:
			players.append(dict(
				number=number, name=name, profile_id=profile_id, civ_id=civ_id,
				team_id=team_id, color_id=color_id, type=ptype
			))
	return players, rms_map_id, started_at


# Map ids that mean a custom map in DE: random map custom, custom map pool, deathmatch custom.
CUSTOM_MAP_IDS = {59, 137, 138}


def _map_name(header_bytes, rms_map_id):
	"""
	Built-in maps are named by id (29 = Arena). Only when the id says "custom map" is the name taken from the
	lobby's mod list ("SUBSCRIBEDMODS:RANDOM_MAPS:nCZ_GoldenLakes_3.2.rms:..."): that list can also hold mods
	the game did not use, so it is never trusted for a built-in map.
	"""
	maps = reference().get('maps', {})
	builtin = maps.get(str(rms_map_id))
	if rms_map_id not in CUSTOM_MAP_IDS and isinstance(builtin, str):
		return builtin
	names = re.findall(rb'RANDOM_MAPS:([^:\x00]{1,80})\.rms', header_bytes[:600000])
	if names:
		return names[-1].decode('utf-8', 'replace').strip()
	return builtin if isinstance(builtin, str) else None


def read_players(raw: bytes):
	from mgz.fast import header as mgz_header
	from mgz.util import Version
	stream = io.BytesIO(raw)
	header = mgz_header.decompress(stream)
	version, game, save, log = mgz_header.parse_version(header, stream)
	if version is not Version.DE:
		raise RecError("Only Definitive Edition recorded games are supported.")
	start = header.tell()
	errors = []
	for extra in (save >= 68.0, save < 68.0):
		header.seek(start)
		try:
			players, rms_map_id, started_at = _read_players(header, save, extra)
			if players and all(1 <= p['number'] <= 8 for p in players):
				return save, players, _map_name(header.getvalue(), rms_map_id), started_at
		except Exception as e:  # try the other layout
			errors.append(str(e))
	raise RecError(f"Could not read the player list (save {save}): {'; '.join(errors)}")


def _clean(payload):
	out = {}
	for k, v in payload.items():
		if k == 'bytes':
			continue
		if isinstance(v, tuple):
			v = list(v)
		if isinstance(v, bytes):
			v = v[0] if len(v) == 1 else v.hex()
		out[k] = v
	return out


def parse(data: bytes) -> dict:
	"""
	Returns dict(save, duration (ms), players [..], actions [{t, a, ...}], syncs [{t, <player>: {...}}], chat [...])
	"""
	from mgz import fast
	from mgz.fast.enums import Operation
	raw = unwrap(data)
	save, players, map_name, started_at = read_players(raw)

	stream = io.BytesIO(raw)
	header_len, = struct.unpack('<I', stream.read(4))
	stream.seek(header_len)
	fast.meta(stream)

	t = 0
	actions, syncs, chat = [], [], []
	while True:
		try:
			op, payload = fast.operation(stream)
		except EOFError:
			break
		except Exception:  # truncated or unknown tail: keep what we have
			break
		if op == Operation.SYNC:
			t += payload[0]
			if payload[2]:
				syncs.append(dict(t=t, players={int(k): v for k, v in payload[2].items() if isinstance(k, int)}))
		elif op == Operation.ACTION:
			action_type, p = payload
			if action_type.name in KEEP_ACTIONS:
				actions.append(dict(t=t, a=action_type.name, **_clean(p)))
		elif op == Operation.CHAT:
			try:
				msg = json.loads(payload.decode('utf-8', 'ignore'))
				chat.append(dict(t=t, player=msg.get('player'), message=msg.get('message', '')))
			except Exception:
				pass

	return dict(save=save, map_name=map_name, started_at=started_at, duration=t, players=players, actions=actions, syncs=syncs, chat=chat)
