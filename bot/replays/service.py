# -*- coding: utf-8 -*-
"""
After a pickup match is reported: find the game on World's Edge, download the
replay uploaded by the highest-rated player, analyse it and open a forum post
where the players can discuss it.
"""
import asyncio
import io
import time
import zipfile

from nextcord import File, ForumChannel, DiscordException

import bot
from core.config import cfg
from core.console import log
from core.database import db
from core.utils import get_nick

from . import worldsedge, rec_parser, analysis, post, insights

db.ensure_table(dict(
	tname="aoe_profiles",
	columns=[
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="profile_id", ctype=db.types.int),
		dict(cname="alias", ctype=db.types.str),
		dict(cname="linked_at", ctype=db.types.int)
	],
	primary_keys=["user_id"]
))

db.ensure_table(dict(
	tname="aoe_replay_posts",
	columns=[
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="game_id", ctype=db.types.int),
		dict(cname="thread_id", ctype=db.types.int),
		dict(cname="at", ctype=db.types.int)
	],
	primary_keys=["match_id"]
))

# Replays usually appear a minute or two after the game ends; keep looking for ~20 minutes.
RETRY_DELAYS = (90, 120, 180, 300, 600)
_running = set()  # bot match ids being processed


class ReplayError(Exception):
	pass


# ---------- account links

async def link(user_id, identifier):
	kind, value = worldsedge.parse_identifier(identifier)
	profiles = await worldsedge.lookup_profiles(
		profile_ids=[value] if kind == 'profile' else None,
		steam_ids=[value] if kind == 'steam' else None
	)
	if not profiles:
		raise ReplayError("No AoE2 player found for that id.")
	profile_id, info = next(iter(profiles.items()))
	await db.insert('aoe_profiles', dict(
		user_id=user_id, profile_id=profile_id, alias=info.get('alias'), linked_at=int(time.time())
	), on_dublicate="replace")
	return profile_id, info


async def unlink(user_id):
	await db.delete('aoe_profiles', where=dict(user_id=user_id))


async def get_link(user_id):
	return await db.select_one(('user_id', 'profile_id', 'alias'), 'aoe_profiles', where=dict(user_id=user_id))


async def get_links(user_ids):
	rows = await db.select(('user_id', 'profile_id', 'alias'), 'aoe_profiles')
	wanted = set(user_ids)
	return {r['user_id']: r['profile_id'] for r in rows if r['user_id'] in wanted}


# ---------- entry points

def schedule(match):
	""" Called when a match is reported. Runs in the background so reporting is never slowed down. """
	if not getattr(match.qc.cfg, 'replay_forum', None) or match.id in _running:
		return
	info = dict(
		match_id=match.id,
		queue=match.queue.name,
		maps=list(match.maps),
		reported_at=int(time.time()),
		players=[dict(user_id=p.id, nick=get_nick(p), rating=match.ratings.get(p.id, 0)) for p in match.players],
	)
	_running.add(match.id)
	asyncio.get_event_loop().create_task(_run(match.qc, info))


async def schedule_from_history(qc, match_id):
	""" Re-run the lookup for an already reported match (moderator command). """
	m = await db.select_one(('match_id', 'at', 'maps', 'queue_name'), 'qc_matches', where=dict(match_id=match_id, channel_id=qc.id))
	if not m:
		raise ReplayError("Match not found in this channel.")
	rows = await db.select(('user_id', 'nick'), 'qc_player_matches', where=dict(match_id=match_id))
	ratings = {p['user_id']: p['rating'] for p in await qc.rating.get_players((r['user_id'] for r in rows))}
	info = dict(
		match_id=match_id, queue=m['queue_name'], maps=(m['maps'] or '').split('\n'), reported_at=m['at'],
		players=[dict(user_id=r['user_id'], nick=r['nick'], rating=ratings.get(r['user_id'], 0)) for r in rows],
	)
	return await _process(qc, info, notify_failure=True)


async def _run(qc, info):
	try:
		for delay in RETRY_DELAYS:
			await asyncio.sleep(delay)
			try:
				if await _process(qc, info, notify_failure=False):
					return
			except ReplayError as e:
				log.info(f"Replay for match {info['match_id']}: {e}")
				if "linked" in str(e):
					return
		await _notify(qc, f"Couldn't find a recorded game for match ({info['match_id']}). "
			f"Make sure players have linked their AoE2 account with `/aoe link` and that someone uploaded the replay.")
	except Exception as e:
		log.error(f"Replay processing failed for match {info['match_id']}: {e}")
	finally:
		_running.discard(info['match_id'])


# ---------- the work

async def _process(qc, info, notify_failure):
	if await db.select_one(('thread_id',), 'aoe_replay_posts', where=dict(match_id=info['match_id'])):
		return True

	links = await get_links([p['user_id'] for p in info['players']])
	if not links:
		raise ReplayError("No players in this match have linked their AoE2 account (`/aoe link`).")

	# Search the history of the highest-rated linked players first.
	by_rating = sorted(info['players'], key=lambda p: p['rating'], reverse=True)
	game = None
	for p in [p for p in by_rating if p['user_id'] in links][:3]:
		matches = await worldsedge.recent_matches(links[p['user_id']])
		game = worldsedge.find_match(matches, links.values(), info['reported_at'])
		if game:
			break
	if not game:
		if notify_failure:
			raise ReplayError("Couldn't find this game in the linked players' World's Edge match history.")
		return False

	urls = worldsedge.replay_urls(game)
	if not urls:
		if notify_failure:
			raise ReplayError("The game was found, but nobody uploaded a recorded game for it.")
		return False

	# BombayBot Elo (the rating used to make the match) for every linked player in the game.
	meta = worldsedge.match_meta(game)
	for p in info['players']:
		if p['user_id'] in links:
			meta.setdefault(links[p['user_id']], {})['elo'] = p['rating']
	try:
		ratings = await worldsedge.lookup_profiles(profile_ids=list(worldsedge.match_profiles(game)))
	except worldsedge.WorldsEdgeError:
		ratings = {}
	for pid, r in ratings.items():
		meta.setdefault(pid, {})['alias'] = r.get('alias')
		if r.get('tg_rating'):
			meta[pid]['rating'] = r['tg_rating']

	# Replay uploaded by the highest-rated player: BombayBot Elo first, World's Edge team rating as a tiebreak.
	def rating_key(pid):
		return (meta.get(pid, {}).get('elo') or 0, meta.get(pid, {}).get('rating') or 0)
	source = max(urls, key=rating_key)
	raw = await worldsedge.download(urls[source])

	rec = await asyncio.to_thread(rec_parser.parse, raw)
	result = await asyncio.to_thread(analysis.analyze, rec, meta)

	map_name = rec.get('map_name') or (game.get('mapname') or '').replace('.rms', '').replace('_', ' ') \
		or (info['maps'][0] if info['maps'] else None)
	await insights.save(
		result, insights.game_key(worldsedge_id=game['id']), map_name=map_name, lobby=game.get('description'),
		match_id=info['match_id'], channel_id=qc.id, source='auto', rec_profile_id=source
	)
	source_name = ratings.get(source, {}).get('alias') or str(source)
	thread = await publish(
		qc, result, rec_bytes=rec_parser.unwrap(raw), map_name=map_name, lobby=game.get('description'),
		title=post.make_title(result, map_name),
		footer=f"World's Edge game {game['id']} · replay uploaded by {source_name}",
		mention_ids=[p['user_id'] for p in info['players']],
	)
	await db.insert('aoe_replay_posts', dict(
		match_id=info['match_id'], channel_id=qc.id, game_id=game['id'], thread_id=thread.id if thread else None,
		at=int(time.time())
	), on_dublicate="replace")
	return True


def _winner_text(result):
	winners = [p for p in result['players'].values() if p['won']]
	if not winners:
		return "result unknown"
	return " & ".join(p['civ'] for p in winners) + " win"


async def publish(qc, result, rec_bytes, map_name=None, lobby=None, title=None, footer=None, mention_ids=(), reply_ctx=None):
	""" Create the forum post (or reply in chat when no forum is set). Returns the thread or None. """
	narrative = None
	if getattr(qc.cfg, 'replay_ai', True) and cfg.GOOGLE_AI_API_KEY:
		try:
			from bot.bombay.ai import generate_replay_summary
			narrative = await generate_replay_summary(post.text_for_ai(result, map_name, lobby))
		except Exception as e:
			log.error(f"Replay AI summary failed: {e}")

	title = title or f"{map_name or 'Game'} — {_winner_text(result)}"
	embed = post.build_embed(result, title, narrative=narrative, footer=footer)
	file = _rec_file(rec_bytes, title)
	mentions = " ".join(f"<@{uid}>" for uid in mention_ids)
	content = (mentions + "\n" if mentions else "") + "Discuss the game here. The recorded game is attached."

	extra = dict(file=file) if file else {}

	forum = getattr(qc.cfg, 'replay_forum', None)
	if forum is None:
		if reply_ctx:
			await reply_ctx.reply(embed=embed, **extra)
		return None

	if isinstance(forum, ForumChannel):
		created = await forum.create_thread(name=title[:100], content=content, embed=embed, **extra)
		thread = getattr(created, 'thread', created)
	else:
		msg = await forum.send(content=content, embed=embed, **extra)
		thread = await msg.create_thread(name=title[:100])

	link = getattr(thread, 'jump_url', None) or f"<#{thread.id}>"
	if reply_ctx:
		await reply_ctx.reply(f"Game breakdown posted: {link}")
	else:
		await _notify(qc, f"Game breakdown for {title}: {link}")
	return thread


def _rec_file(rec_bytes, title):
	if not rec_bytes:
		return None
	buf = io.BytesIO()
	with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
		z.writestr('game.aoe2record', rec_bytes)
	if buf.tell() > 9 * 1024 * 1024:  # stay under Discord's default upload limit
		return None
	buf.seek(0)
	safe = "".join(c for c in title if c.isalnum() or c in " -_()").strip().replace(" ", "_")[:60] or "game"
	return File(buf, filename=f"{safe}.zip")


async def _notify(qc, text):
	try:
		ctx = bot.SystemContext(qc)
		await ctx.notice(text)
	except (DiscordException, IndexError) as e:
		log.error(f"Replay notice failed: {e}")


async def analyze_upload(qc, ctx, data: bytes, filename: str, summary: bool = True):
	""" /replay upload: analyse a recorded game attached by a user; post the breakdown unless summary is off. """
	rec = await asyncio.to_thread(rec_parser.parse, data)
	profile_ids = [p['profile_id'] for p in rec['players'] if p['profile_id']]
	meta = {}
	try:
		for pid, r in (await worldsedge.lookup_profiles(profile_ids=profile_ids)).items():
			meta[pid] = dict(alias=r.get('alias'), rating=r.get('tg_rating'))
	except Exception:
		pass  # World's Edge names and ratings are optional
	# BombayBot Elo for players who linked their AoE2 account
	rows = await db.select(('user_id', 'profile_id'), 'aoe_profiles')
	user_of = {r['profile_id']: r['user_id'] for r in rows if r['profile_id'] in profile_ids}
	if user_of:
		elos = {r['user_id']: r['rating'] for r in await qc.rating.get_players(user_of.values())}
		for pid, uid in user_of.items():
			if elos.get(uid) is not None:
				meta.setdefault(pid, {})['elo'] = elos[uid]
	result = await asyncio.to_thread(analysis.analyze, rec, meta)
	saved = await insights.save(
		result, insights.game_key(filename=filename, rec_bytes=rec_parser.unwrap(data)), map_name=rec.get('map_name'),
		channel_id=qc.id, source='upload', uploaded_by=ctx.author.id
	)
	title = post.make_title(result, rec.get('map_name'))
	if not summary:
		if not saved:
			raise ReplayError("The recorded game was read, but its stats could not be saved.")
		await ctx.success(f"Saved player stats from **{title}** ({len(result['players'])} players). No summary posted.")
		return
	await publish(
		qc, result, rec_bytes=rec_parser.unwrap(data), map_name=rec.get('map_name'), title=title,
		footer=f"Uploaded by {get_nick(ctx.author)}", reply_ctx=ctx,
	)
