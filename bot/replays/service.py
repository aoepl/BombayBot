# -*- coding: utf-8 -*-
"""
Game breakdowns from recorded games: analyse the game, save every player's performance (insights.py) and
post the breakdown once per game (in the replay forum when one is set, otherwise in the queue channel).

Games arrive two ways:
- /replay upload: a member uploads the recording.
- Automatically after a pickup match is reported with /report (not /report abort), only when the queue channel's replay forum is a forum
  channel: the bot finds the game in the World's Edge match history of the highest-rated player whose AoE2
  profile it knows, downloads that player's recording and posts it. It stops looking as soon as a member
  uploads the same game.

Posts never attach the recording (to save the server's Discord storage); they link to the official
download and the match pages instead. Profile ids are learned from uploaded recordings: every recording
lists each player's profile id, saved against the member their name matched.
"""
import asyncio
import re
import time

from nextcord import ForumChannel, DiscordException, NotFound

import bot
from core.client import dc
from core.config import cfg
from core.console import log
from core.database import db
from core.utils import get_nick

from . import rec_parser, analysis, post, insights, worldsedge

# One post per game. game_key/fingerprint match insights.
db.ensure_table(dict(
	tname="aoe_game_posts",
	columns=[
		dict(cname="game_key", ctype=db.types.str),
		dict(cname="fingerprint", ctype=db.types.str),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="thread_id", ctype=db.types.int),
		dict(cname="message_id", ctype=db.types.int),
		dict(cname="post_url", ctype=db.types.str),
		dict(cname="at", ctype=db.types.int)
	],
	primary_keys=["game_key"]
))
_posting = set()  # game keys / fingerprints being posted right now

# In-game names linked to Discord members by hand (/aoe link). One member can have several names.
db.ensure_table(dict(
	tname="aoe_name_links",
	columns=[
		dict(cname="name_key", ctype=db.types.str),
		dict(cname="name", ctype=db.types.str),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="linked_by", ctype=db.types.int),
		dict(cname="at", ctype=db.types.int)
	],
	primary_keys=["name_key"]
))


# AoE2 profile ids of members, learned from recordings (a member can have several accounts).
db.ensure_table(dict(
	tname="aoe_profiles",
	columns=[
		dict(cname="profile_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="name", ctype=db.types.str),
		dict(cname="at", ctype=db.types.int)
	],
	primary_keys=["profile_id"]
))

# Recorded games usually appear a minute or two after the game ends; keep looking for about 20 minutes.
RETRY_DELAYS = (90, 120, 180, 300, 600)
_pending = {}  # bot match id -> dict(qc_id, user_ids, profile_ids, reported_at, cancelled)


class ReplayError(Exception):
	pass


# ---------- AoE2 profile ids

async def learn_profiles(rec, found):
	""" Save the profile id of every player whose name matched a member. """
	now = int(time.time())
	for p in rec['players']:
		info = found.get(p['name'])
		if info and info.get('user_id') and p.get('profile_id'):
			try:
				await db.insert('aoe_profiles', dict(
					profile_id=p['profile_id'], user_id=info['user_id'], name=p['name'], at=now
				), on_dublicate="replace")
			except Exception as e:
				log.error(f"Could not save AoE2 profile {p['profile_id']}: {e}")


async def profiles_of(user_ids):
	""" {user_id: [profile ids, most recently seen first]} """
	wanted = set(user_ids)
	out = {}
	rows = await db.select(('profile_id', 'user_id', 'at'), 'aoe_profiles')
	for r in sorted(rows, key=lambda r: -(r.get('at') or 0)):
		if r['user_id'] in wanted:
			out.setdefault(r['user_id'], []).append(r['profile_id'])
	return out


# ---------- finding a recorded game on World's Edge

def _links(match_id, profile_ids, urls=None):
	""" Links for a post: official download (from a player whose recording was uploaded) and match pages. """
	if not match_id:
		return None
	urls = urls or {}
	source = next((p for p in profile_ids if p in urls), None) or (next(iter(urls), None)) or \
		next(iter(profile_ids), None)
	links = worldsedge.match_links(match_id)
	if source:
		links['replay'] = worldsedge.replay_link(match_id, source)
	return links


async def locate(rec, filename=None, players_by_elo=()):
	"""
	The World's Edge match a recording came from: (match id, match entry or None). The id comes from the file
	name (AgeIIDE_Replay_<id>) when there is one; otherwise the match history of up to three players (highest
	BombayBot Elo first) is searched for a match with the same players that started at the same time.
	"""
	named = None
	if filename and (m := re.search(r"Replay_(\d{6,})", filename)):
		named = int(m.group(1))
	profile_ids = [p['profile_id'] for p in rec['players'] if p.get('profile_id')]
	order = [p for p in players_by_elo if p in profile_ids] + [p for p in profile_ids if p not in players_by_elo]
	for pid in order[:3]:
		try:
			matches = await worldsedge.recent_matches(pid)
		except Exception as e:
			log.info(f"World's Edge lookup for profile {pid} failed: {e}")
			continue
		if named:
			game = next((m for m in matches if m.get('id') == named), None)
		else:
			game = worldsedge.find_by_start(matches, profile_ids, rec.get('started_at'))
		if game:
			return game['id'], game
	return named, None


# ---------- BombayBot Elo by in-game name

def _norm(name):
	""" Compare names without rating tags, case, spaces or symbols: "[1650] Link.Chaos" == "link_chaos". """
	name = re.sub(r"^\[\d+\]\s*", "", name or "")
	return re.sub(r"[^0-9a-z]", "", name.lower())


async def link_name(name, user_id, linked_by):
	""" Link an in-game name to a member. Returns the member it was linked to before, if any. """
	key = _norm(name)
	if not key:
		raise ReplayError("That in-game name has no letters or numbers in it.")
	old = await db.select_one(('user_id',), 'aoe_name_links', where=dict(name_key=key))
	await db.insert('aoe_name_links', dict(
		name_key=key, name=name.strip(), user_id=user_id, linked_by=linked_by, at=int(time.time())
	), on_dublicate="replace")
	return old['user_id'] if old and old['user_id'] != user_id else None


async def name_owner(name):
	row = await db.select_one(('user_id',), 'aoe_name_links', where=dict(name_key=_norm(name)))
	return row['user_id'] if row else None


async def unlink_name(name):
	key = _norm(name)
	if not await db.select_one(('name_key',), 'aoe_name_links', where=dict(name_key=key)):
		return False
	await db.delete('aoe_name_links', where=dict(name_key=key))
	return True


async def linked_names(user_id):
	return [r['name'] for r in await db.select(('name', 'user_id'), 'aoe_name_links', where=dict(user_id=user_id))]


async def bombay_players_by_name(qc, names):
	"""
	Match in-game names to members: names linked with /aoe link first, then the member's BombayBot nick and
	Discord names. Returns {in-game name: dict(user_id, elo)} for names that match exactly one member.
	"""
	wanted = {_norm(n): n for n in names if _norm(n)}
	linked = {}
	for key in wanted:
		if row := await db.select_one(('user_id',), 'aoe_name_links', where=dict(name_key=key)):
			linked[key] = row['user_id']
	candidates = {key: {uid} for key, uid in linked.items()}  # normalised name -> set(user_id)

	def add(name, user_id):
		key = _norm(name)
		if key in wanted and key not in linked:
			candidates.setdefault(key, set()).add(user_id)

	for row in await db.select(('user_id', 'nick'), 'qc_players', where=dict(channel_id=qc.rating.channel_id)):
		add(row['nick'], row['user_id'])
	guild = getattr(dc.get_channel(qc.id), 'guild', None)
	if guild:
		for m in guild.members:
			for name in (m.name, getattr(m, 'global_name', None), m.nick, m.display_name):
				add(name, m.id)

	matched = {key: next(iter(ids)) for key, ids in candidates.items() if len(ids) == 1}
	if not matched:
		return {}
	elos = {r['user_id']: r['rating'] for r in await qc.rating.get_players(list(matched.values()))}
	return {wanted[key]: dict(user_id=uid, elo=elos.get(uid), via='linked name' if key in linked else 'Discord name')
		for key, uid in matched.items()}


def elo_report(rec, found):
	""" One line for the uploader: who got a BombayBot Elo (and how), and who could not be matched. """
	got = [f"{n} {i['elo']} ({i['via']})" for n, i in found.items() if i.get('elo') is not None]
	missing = [p['name'] for p in rec['players'] if p['name'] not in found or found[p['name']].get('elo') is None]
	text = f"Elo found: {', '.join(got)}." if got else "No BombayBot Elo found for any player."
	if missing:
		text += f" No match: {', '.join(missing)} (link with `/aoe link`)."
	return text


# ---------- one post per game

async def _live_post(key=None, fingerprint=None):
	"""
	The stored post for this game if it still exists in Discord. When someone deleted the post,
	its record is removed so the game can be posted again.
	"""
	rows = []
	for col, value in (('game_key', key), ('fingerprint', fingerprint)):
		if value:
			rows += await db.select(('game_key', 'channel_id', 'thread_id', 'message_id', 'post_url'),
				'aoe_game_posts', where={col: value})
	for row in rows:
		if await _post_exists(row):
			return row
		await db.delete('aoe_game_posts', where=dict(game_key=row['game_key']))
	return None


async def _post_exists(row):
	try:
		if row.get('message_id'):
			channel = dc.get_channel(row['channel_id']) or await dc.fetch_channel(row['channel_id'])
			await channel.fetch_message(row['message_id'])
			return True
		if row.get('thread_id'):
			thread = dc.get_channel(row['thread_id']) or await dc.fetch_channel(row['thread_id'])
			return thread is not None
		return False  # nothing to check against: allow a fresh post
	except NotFound:
		return False
	except DiscordException:
		return True  # can't tell (permissions, outage): don't risk a duplicate


async def _record_post(key, fp, posted):
	if not posted:
		return
	await db.insert('aoe_game_posts', dict(
		game_key=key, fingerprint=fp, channel_id=posted['channel_id'], thread_id=posted.get('thread_id'),
		message_id=posted.get('message_id'), post_url=posted.get('url'), at=int(time.time())
	), on_dublicate="replace")


def _winner_text(result):
	winners = [p for p in result['players'].values() if p['won']]
	if not winners:
		return "result unknown"
	return " & ".join(p['civ'] for p in winners) + " win"


async def publish(qc, result, map_name=None, lobby=None, title=None, footer=None, reply_ctx=None, reply_extra=None,
		links=None, mention_ids=None):
	"""
	Create the forum post (or, with no forum set, a message in the queue channel). The recording is never
	attached: the post links to the official download instead.
	Returns dict(channel_id, thread_id, message_id, url) describing the post.
	"""
	title = title or f"{map_name or 'Game'} — {_winner_text(result)}"
	embed = await _render(qc, result, title, map_name, lobby, footer, links)
	content = "Discuss the game here."
	if mention_ids:
		content += " " + " ".join(f"<@{u}>" for u in mention_ids)
	extra = {}

	forum = getattr(qc.cfg, 'replay_forum', None)
	if forum is None:
		channel = dc.get_channel(qc.id)
		msg = await channel.send(embed=embed, **extra)
		posted = dict(channel_id=channel.id, message_id=msg.id, url=msg.jump_url)
	elif isinstance(forum, ForumChannel):
		created = await forum.create_thread(name=title[:100], content=content, embed=embed, **extra)
		thread = getattr(created, 'thread', created)
		posted = dict(channel_id=forum.id, thread_id=thread.id, url=getattr(thread, 'jump_url', None))
	else:
		msg = await forum.send(content=content, embed=embed, **extra)
		thread = await msg.create_thread(name=title[:100])
		posted = dict(channel_id=forum.id, message_id=msg.id, thread_id=thread.id, url=getattr(thread, 'jump_url', None))

	link = posted['url'] or (f"<#{posted['thread_id']}>" if posted.get('thread_id') else "")
	if reply_ctx:
		await reply_ctx.reply(f"Game breakdown posted: {link}" + (f"\n{reply_extra}" if reply_extra else ""))
	elif forum is not None:
		await _notify(qc, f"Game breakdown for {title}: {link}")
	return posted


async def _render(qc, result, title, map_name=None, lobby=None, footer=None, links=None):
	narrative = None
	if getattr(qc.cfg, 'replay_ai', True) and cfg.GOOGLE_AI_API_KEY:
		try:
			from bot.bombay.ai import generate_replay_summary
			narrative = await generate_replay_summary(post.text_for_ai(result, map_name, lobby))
		except Exception as e:
			log.error(f"Replay AI summary failed: {e}")
	return post.build_embed(result, title, narrative=narrative, footer=footer, links=links)


async def update_post(qc, row, result, title, map_name=None, footer=None, links=None):
	"""
	Re-render an existing post with a fresh analysis (new Elo links, new scoring rules).
	Forum posts: the starter message has the thread's id. Returns True when the post was edited.
	"""
	try:
		embed = await _render(qc, result, title, map_name, footer=footer, links=links)
		if row.get('message_id'):
			channel = dc.get_channel(row['channel_id']) or await dc.fetch_channel(row['channel_id'])
			msg = await channel.fetch_message(row['message_id'])
		else:
			thread = dc.get_channel(row['thread_id']) or await dc.fetch_channel(row['thread_id'])
			msg = await thread.fetch_message(row['thread_id'])
		await msg.edit(embed=embed)
		if row.get('thread_id'):
			thread = dc.get_channel(row['thread_id']) or await dc.fetch_channel(row['thread_id'])
			if getattr(thread, 'name', title[:100]) != title[:100]:
				await thread.edit(name=title[:100])
		return True
	except Exception as e:  # never fail the upload because the old post could not be edited
		log.error(f"Could not update replay post: {e}")
		return False


async def _notify(qc, text):
	try:
		ctx = bot.SystemContext(qc)
		await ctx.notice(text)
	except (DiscordException, IndexError) as e:
		log.error(f"Replay notice failed: {e}")


def _players_by_elo(rec, found):
	""" Profile ids of the recording's players, highest BombayBot Elo first. """
	elo = {p['profile_id']: (found.get(p['name']) or {}).get('elo') or 0 for p in rec['players']}
	return sorted(elo, key=lambda p: -elo[p])


async def analyze_upload(qc, ctx, data: bytes, filename: str, summary: bool = True):
	"""
	/replay upload: analyse a recorded game and post the breakdown right away (unless summary is off).
	The World's Edge lookup for the game links runs afterwards and adds them to the post when it finds the game,
	so it never delays the post. A match id in the file name gives the links straight away.
	"""
	rec = await asyncio.to_thread(rec_parser.parse, data)
	fp = insights.fingerprint(rec)
	found = await bombay_players_by_name(qc, [p['name'] for p in rec['players']])
	log.info(f"Replay Elo lookup: {found}")
	report = elo_report(rec, found)
	await learn_profiles(rec, found)
	cancel_pending(rec, found)  # a member uploaded it: the automatic search for this game can stop

	by_elo = _players_by_elo(rec, found)
	lookup = bool(getattr(qc.cfg, 'replay_lookup', 1))
	named = None
	if lookup and (m := re.search(r"Replay_(\d{6,})", filename or '')):
		named = int(m.group(1))
	meta = {}
	for name, info in found.items():
		for p in rec['players']:
			if p['name'] == name:
				meta[p['profile_id']] = dict(elo=info['elo'], user_id=info['user_id'])
	key = insights.game_key(filename=filename, rec=rec)
	footer = f"Uploaded by {get_nick(ctx.author)}"
	# Analysed already by this version of the bot, with the same Elos: nothing to redo.
	stale, old_version, saved_key = await _needs_analysis(key, fp, rec, found)
	if not stale:
		existing = await _live_post(key=saved_key, fingerprint=fp) if summary else None
		url = existing.get('post_url') if existing else None
		where = f": {url}" if url else "."
		what = f"already posted{where}" if existing else "already analysed."
		await ctx.success(f"This game was {what} It was analysed with the current version of the bot "
			f"(v{insights.ANALYSIS_VERSION}) and the same Elos, so nothing changed.\n{report}")
		return
	result = await asyncio.to_thread(analysis.analyze, rec, meta)
	saved = await insights.save(
		result, key, map_name=rec.get('map_name'), channel_id=qc.id, source='upload',
		uploaded_by=ctx.author.id, fingerprint=fp
	)
	key = saved or key
	title = post.make_title(result, rec.get('map_name'))
	links = _links(named, by_elo) if named else None
	later = dict(rec=rec, filename=filename, by_elo=by_elo, result=result, key=key, fp=fp, uploaded_by=ctx.author.id)
	if not summary:
		if not saved:
			raise ReplayError("The recorded game was read, but its stats could not be saved.")
		await ctx.success(f"Saved player stats from **{title}** ({len(result['players'])} players). No summary posted.\n{report}")
		if lookup:
			_spawn(_links_later(qc, None, **later))  # still find the World's Edge id, so the game is known by it
		return

	existing = await _live_post(key=key, fingerprint=fp)
	if existing or key in _posting or fp in _posting:
		url = existing.get('post_url') if existing else None
		where = f": {url}" if url else "."
		why = (f"re-analysed with the newer bot version (v{old_version} → v{insights.ANALYSIS_VERSION})"
			if old_version is not None and old_version < insights.ANALYSIS_VERSION else "updated with the latest Elo and stats")
		if existing and await update_post(qc, existing, result, title, rec.get('map_name'), footer=footer, links=links):
			await ctx.success(f"This game was already posted, so the post was {why}{where}\n{report}")
			if lookup and not links:
				_spawn(_links_later(qc, existing, **later))
		else:
			await ctx.success(f"This game has already been posted{where} Player stats were refreshed.\n{report}")
		return
	_posting.update((key, fp))
	try:
		posted = await publish(
			qc, result, map_name=rec.get('map_name'), title=title,
			footer=footer, reply_ctx=ctx, reply_extra=report, links=links,
		)
		await _record_post(key, fp, posted)
	finally:
		_posting.difference_update((key, fp))
	if lookup and posted:
		row = dict(channel_id=posted['channel_id'], thread_id=posted.get('thread_id'), message_id=posted.get('message_id'),
			post_url=posted.get('url'))
		_spawn(_links_later(qc, row, **later))


async def _needs_analysis(key, fp, rec, found):
	"""
	(analyse again?, version it was analysed with, key it is saved under). A game needs analysing when it was
	never saved, was saved by an older version of the bot (scoring rules changed), or a player's BombayBot Elo
	match changed since (e.g. after /aoe link).
	"""
	row = None
	for col, value in (('game_key', key), ('fingerprint', fp)):
		if value and not row:
			row = await db.select_one(('game_key', 'analysis_version'), 'aoe_games', where={col: value})
	if not row:
		return True, None, key
	version = row.get('analysis_version')
	if version is None or version < insights.ANALYSIS_VERSION:
		return True, version, row['game_key']
	saved = {r['name']: r.get('elo') for r in await db.select(('name', 'elo'), 'aoe_game_players',
		where=dict(game_key=row['game_key']))}
	for p in rec['players']:
		now = (found.get(p['name']) or {}).get('elo')
		if p['name'] not in saved or saved[p['name']] != now:
			return True, version, row['game_key']
	return False, version, row['game_key']


def _spawn(coro):
	asyncio.get_event_loop().create_task(coro)


async def _links_later(qc, row, rec, filename, by_elo, result, key, fp, uploaded_by):
	"""
	After the post is up: find the game on World's Edge, store it under its match id (so the automatic fetch and
	later uploads know it) and add the download and match links to the post.
	"""
	try:
		match_id, game = await locate(rec, filename, by_elo)
		if not match_id:
			log.info("Replay links: game not found on World's Edge.")
			return
		we_key = insights.game_key(worldsedge_id=match_id)
		if we_key != key:
			await insights.save(
				result, we_key, map_name=rec.get('map_name'), lobby=game.get('description') if game else None,
				channel_id=qc.id, source='upload', uploaded_by=uploaded_by, fingerprint=fp
			)
			if row:
				await _record_post(we_key, fp, dict(channel_id=row['channel_id'], thread_id=row.get('thread_id'),
					message_id=row.get('message_id'), url=row.get('post_url')))
		if row:
			await _add_links(row, _links(match_id, by_elo, worldsedge.replay_urls(game) if game else None))
	except Exception as e:  # links are a bonus: never let them break anything
		log.error(f"Replay links failed: {e}")


async def _add_links(row, links):
	""" Edit the posted breakdown to add the game links, without re-rendering it (no new AI summary). """
	if row.get('message_id'):
		channel = dc.get_channel(row['channel_id']) or await dc.fetch_channel(row['channel_id'])
		msg = await channel.fetch_message(row['message_id'])
	else:
		thread = dc.get_channel(row['thread_id']) or await dc.fetch_channel(row['thread_id'])
		msg = await thread.fetch_message(row['thread_id'])
	if msg.embeds:
		await msg.edit(embed=post.with_links(msg.embeds[0], links))


# ---------- automatic fetch after a pickup match is reported

def _auto_enabled(qc):
	""" Only with a dedicated replay forum (a forum channel set as replay_forum) and replay_auto on. """
	return bool(getattr(qc.cfg, 'replay_auto', 1)) and isinstance(getattr(qc.cfg, 'replay_forum', None), ForumChannel)


def schedule(match):
	""" Called after /report loss or draw (never abort). Runs in the background so reporting is never slowed down. """
	if not _auto_enabled(match.qc) or match.id in _pending:
		return
	info = dict(
		match_id=match.id,
		qc_id=match.qc.id,
		maps=list(match.maps),
		reported_at=int(time.time()),
		players=[dict(user_id=p.id, rating=match.ratings.get(p.id, 0)) for p in match.players],
		user_ids={p.id for p in match.players},
		profile_ids=set(),
		cancelled=False,
	)
	_pending[match.id] = info
	asyncio.get_event_loop().create_task(_run(match.qc, info))


def cancel_pending(rec, found):
	"""
	A member uploaded a recording: stop the automatic search for the reported match it belongs to, i.e. one
	that at least half of its players (by Discord member or by AoE2 profile) took part in.
	"""
	users = {i['user_id'] for i in found.values() if i.get('user_id')}
	profiles = {p['profile_id'] for p in rec['players'] if p.get('profile_id')}
	for info in _pending.values():
		same_users = len(users & info['user_ids']) * 2 >= max(1, len(info['user_ids']))
		same_profiles = info['profile_ids'] and len(profiles & info['profile_ids']) * 2 >= len(info['profile_ids'])
		if same_users or same_profiles:
			info['cancelled'] = True
			log.info(f"Replay for match {info['match_id']}: uploaded by a member, automatic search stopped.")


async def _run(qc, info):
	try:
		for delay in RETRY_DELAYS:
			await asyncio.sleep(delay)
			if info['cancelled']:
				return
			try:
				if await _process(qc, info):
					return
			except ReplayError as e:
				log.info(f"Replay for match {info['match_id']}: {e}")
				if e.args and e.args[0].startswith("No known"):
					return
			except Exception as e:
				log.info(f"Replay for match {info['match_id']}: World's Edge not reachable ({e}); will retry.")
		if not info['cancelled']:
			await _notify(qc, f"Couldn't find the recorded game for match ({info['match_id']}) on World's Edge. "
				f"Upload it with `/replay upload` to get the game breakdown.")
	except Exception as e:
		log.error(f"Replay processing failed for match {info['match_id']}: {e}")
	finally:
		_pending.pop(info['match_id'], None)


async def _process(qc, info):
	""" One attempt. True when the game is posted (or turned out to be posted already). """
	links = await profiles_of(info['user_ids'])
	if not links:
		raise ReplayError("No known AoE2 profiles for these players yet: they are learned from uploaded recordings.")
	info['profile_ids'] = {pid for pids in links.values() for pid in pids}
	rating = {p['user_id']: p['rating'] for p in info['players']}

	game = None
	for uid in sorted(links, key=lambda u: -rating.get(u, 0))[:3]:  # highest-rated players first
		matches = await worldsedge.recent_matches(links[uid][0])
		game = worldsedge.find_match(matches, info['profile_ids'], info['reported_at'])
		if game:
			break
	if not game:
		return False
	key = insights.game_key(worldsedge_id=game['id'])
	if info['cancelled'] or key in _posting or await _live_post(key=key):
		return True
	urls = worldsedge.replay_urls(game)
	if not urls:
		return False  # the game is there but no recording was uploaded yet

	profile_user = {pid: uid for uid, pids in links.items() for pid in pids}
	source = max(urls, key=lambda pid: rating.get(profile_user.get(pid), 0))  # highest-rated player's recording
	raw = await worldsedge.download(urls[source])
	rec = await asyncio.to_thread(rec_parser.parse, raw)
	fp = insights.fingerprint(rec)
	if info['cancelled'] or fp in _posting or await _live_post(key=key, fingerprint=fp):
		return True

	meta = worldsedge.match_meta(game)
	for pid, uid in profile_user.items():
		meta.setdefault(pid, {}).update(elo=rating.get(uid), user_id=uid)
	result = await asyncio.to_thread(analysis.analyze, rec, meta)
	map_name = rec.get('map_name') or (info['maps'][0] if info['maps'] else None)
	key = await insights.save(
		result, key, map_name=map_name, lobby=game.get('description'), match_id=info['match_id'],
		channel_id=qc.id, source='auto', rec_profile_id=source, fingerprint=fp
	) or key
	source_name = next((p['name'] for p in rec['players'] if p.get('profile_id') == source), str(source))
	_posting.update((key, fp))
	try:
		posted = await publish(
			qc, result, map_name=map_name, lobby=game.get('description'), title=post.make_title(result, map_name),
			footer=f"World's Edge game {game['id']} · recorded game of {source_name}",
			links=_links(game['id'], [source], urls), mention_ids=sorted(info['user_ids']),
		)
		await _record_post(key, fp, posted)
	finally:
		_posting.difference_update((key, fp))
	return True
