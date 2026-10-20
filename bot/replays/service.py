# -*- coding: utf-8 -*-
"""
Game breakdowns from recorded games that members upload with /replay upload:
analyse the game, save every player's performance (insights.py) and post the breakdown
once per game (in the replay forum when one is set, otherwise in the queue channel).
"""
import asyncio
import io
import re
import time
import zipfile

from nextcord import File, ForumChannel, DiscordException, NotFound

import bot
from core.client import dc
from core.config import cfg
from core.console import log
from core.database import db
from core.utils import get_nick

from . import rec_parser, analysis, post, insights

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


class ReplayError(Exception):
	pass


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


async def publish(qc, result, rec_bytes, map_name=None, lobby=None, title=None, footer=None, reply_ctx=None, reply_extra=None):
	"""
	Create the forum post (or, with no forum set, a message in the queue channel).
	Returns dict(channel_id, thread_id, message_id, url) describing the post.
	"""
	title = title or f"{map_name or 'Game'} — {_winner_text(result)}"
	embed = await _render(qc, result, title, map_name, lobby, footer)
	file = _rec_file(rec_bytes, title)
	content = "Discuss the game here. The recorded game is attached."
	extra = dict(file=file) if file else {}

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


async def _render(qc, result, title, map_name=None, lobby=None, footer=None):
	narrative = None
	if getattr(qc.cfg, 'replay_ai', True) and cfg.GOOGLE_AI_API_KEY:
		try:
			from bot.bombay.ai import generate_replay_summary
			narrative = await generate_replay_summary(post.text_for_ai(result, map_name, lobby))
		except Exception as e:
			log.error(f"Replay AI summary failed: {e}")
	return post.build_embed(result, title, narrative=narrative, footer=footer)


async def update_post(qc, row, result, title, map_name=None, footer=None):
	"""
	Re-render an existing post with a fresh analysis (new Elo links, new scoring rules).
	Forum posts: the starter message has the thread's id. Returns True when the post was edited.
	"""
	try:
		embed = await _render(qc, result, title, map_name, footer=footer)
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
	""" /replay upload: analyse a recorded game; post the breakdown unless summary is off. """
	rec = await asyncio.to_thread(rec_parser.parse, data)
	fp = insights.fingerprint(rec)
	meta = {}
	found = await bombay_players_by_name(qc, [p['name'] for p in rec['players']])
	log.info(f"Replay Elo lookup: {found}")
	report = elo_report(rec, found)
	for name, info in found.items():
		for p in rec['players']:
			if p['name'] == name:
				meta[p['profile_id']] = dict(elo=info['elo'], user_id=info['user_id'])
	result = await asyncio.to_thread(analysis.analyze, rec, meta)
	key = insights.game_key(filename=filename, rec=rec)
	saved = await insights.save(
		result, key, map_name=rec.get('map_name'), channel_id=qc.id, source='upload',
		uploaded_by=ctx.author.id, fingerprint=fp
	)
	key = saved or key
	title = post.make_title(result, rec.get('map_name'))
	if not summary:
		if not saved:
			raise ReplayError("The recorded game was read, but its stats could not be saved.")
		await ctx.success(f"Saved player stats from **{title}** ({len(result['players'])} players). No summary posted.\n{report}")
		return

	existing = await _live_post(key=key, fingerprint=fp)
	if existing or key in _posting or fp in _posting:
		url = existing.get('post_url') if existing else None
		where = f": {url}" if url else "."
		if existing and await update_post(qc, existing, result, title, rec.get('map_name'), footer=f"Uploaded by {get_nick(ctx.author)}"):
			await ctx.success(f"This game was already posted, so the post was updated with the latest Elo and stats{where}\n{report}")
		else:
			await ctx.success(f"This game has already been posted{where} Player stats were refreshed.\n{report}")
		return
	_posting.update((key, fp))
	try:
		posted = await publish(
			qc, result, rec_bytes=rec_parser.unwrap(data), map_name=rec.get('map_name'), title=title,
			footer=f"Uploaded by {get_nick(ctx.author)}", reply_ctx=ctx, reply_extra=report,
		)
		await _record_post(key, fp, posted)
	finally:
		_posting.difference_update((key, fp))
