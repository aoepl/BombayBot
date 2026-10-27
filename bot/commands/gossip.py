# -*- coding: utf-8 -*-
"""
/gossip: an AI recap of the quarrels in this channel (and any other channels given) over the last day or week, in a
light, fun tone: who argued with whom and about what, who enjoyed watching, and who had the receipts.

People who ran /gossip_optout are left out completely: their messages, replies to them and messages that mention them
are never sent to the AI. Only channels both the bot and the person asking can read are used.
"""
import re
import time
from datetime import datetime, timedelta, timezone

from nextcord import TextChannel, Embed

from core.database import db
from core.utils import get_nick

import bot

PERIODS = {'1d': 1, '2d': 2, '3d': 3, '7d': 7}
MAX_MESSAGES = 4000          # most recent people's messages (not bots, not queue commands) used per run
READ_LIMIT = 20000           # messages read per channel at most, to find them
# Queue commands carry no conversation: ++, --, +domestic, -1v1, ++ domestic ... (and anything starting with the
# channel's command prefix, e.g. !add).
QUEUE_COMMAND = re.compile(r'^(?:(?:\+\+|--)\s*\S*|[+-][A-Za-z0-9]\S*)$')
MAX_CHARS = 150_000          # chat sent to the AI (the most recent part, when there is more)
MESSAGE_CHARS = 400          # one message is cut to this
REACTION_REQUESTS = 50       # Discord requests for who reacted, only on messages in or just after the quarrels
REACTORS_SHOWN = 10          # names per emoji (the rest as +n)
AFTER_QUARREL = 15           # messages after a quarrel whose reactions still count as watching it
COOLDOWN_S = 10 * 60         # per queue channel, so the channel isn't flooded
_last_run = {}

db.ensure_table(dict(
	tname="gossip_optout",
	columns=[
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="at", ctype=db.types.int),
	],
	primary_keys=["user_id"]
))


async def _opted_out():
	return {r['user_id'] for r in await db.select(('user_id',), 'gossip_optout')}


async def gossip_optout(ctx):
	""" Switch the author's opt-out from /gossip on or off. """
	if ctx.author.id in await _opted_out():
		await db.delete('gossip_optout', where=dict(user_id=ctx.author.id))
		await ctx.reply_dm("You're back in: /gossip can mention you again.")
	else:
		await db.insert('gossip_optout', dict(user_id=ctx.author.id, at=int(time.time())), on_dublicate='replace')
		await ctx.reply_dm("You're opted out: /gossip will leave you out completely (your messages, replies to you and "
			"messages that mention you). Run /gossip_optout again to opt back in.")


def _readable(channel, member):
	perms = channel.permissions_for(member)
	return perms.view_channel and perms.read_message_history


def _reactions(msg, reactors):
	""" [😂 Morty, Orca +1 · 🍿×2]: who reacted where it was looked up (reactors: {message id: {emoji: names}}). """
	parts = []
	for r in msg.reactions:
		who = (reactors.get(msg.id) or {}).get(str(r.emoji))
		if who:
			more = r.count - len(who)
			parts.append(f"{r.emoji} {', '.join(who)}" + (f" +{more}" if more > 0 else ''))
		else:
			parts.append(f"{r.emoji}×{r.count}")
	return f" [{' · '.join(parts)}]" if parts else ''


def _line(msg, num, ids, names, reactors):
	""" One message as a line for the AI: #12 [Mon 21:04] Name (replying to #9 Other): text [😂 Morty, Orca · 🍿×2] """
	text = msg.clean_content.replace('\n', ' ').strip()
	if len(text) > MESSAGE_CHARS:
		text = text[:MESSAGE_CHARS] + '…'
	if msg.attachments:
		text += ' [attachment]'
	if msg.stickers:
		text += ' [sticker]'
	if not text:
		return None
	reply = ''
	ref = msg.reference.message_id if msg.reference else None
	if ref in ids:
		reply = f" (replying to #{ids[ref][0]} {ids[ref][1]})"
	return f"#{num} [{msg.created_at.strftime('%a %H:%M')}] {names[msg.author.id]}{reply}: {text}" + _reactions(msg, reactors)


async def _who_reacted(kept, ids, spans, out):
	"""
	Who reacted to the messages in or just after the quarrels (spans: [(first #, last #)]), most-reacted first, at
	most REACTION_REQUESTS requests. Reactions are read now, so they can differ a little from the time of the quarrel.
	"""
	wanted = [m for _, m in kept if m.reactions and any(a <= ids[m.id][0] <= b + AFTER_QUARREL for a, b in spans)]
	wanted.sort(key=lambda m: -sum(r.count for r in m.reactions))
	reactors, requests = {}, 0
	for m in wanted:
		for r in m.reactions:
			if requests >= REACTION_REQUESTS:
				return reactors
			requests += 1
			try:
				users = [u async for u in r.users(limit=REACTORS_SHOWN + 5)]
			except Exception:  # noqa: BLE001 - deleted message or missing permission: keep the count only
				continue
			who = [getattr(u, 'display_name', None) or u.name for u in users if not u.bot and u.id not in out]
			if who:
				reactors.setdefault(m.id, {})[str(r.emoji)] = who[:REACTORS_SHOWN]
	return reactors


async def gossip(ctx, period: str = '1d', channel_2: TextChannel = None, channel_3: TextChannel = None):
	from bot.bombay.ai import generate_gossip, find_quarrels

	days = PERIODS.get(period)
	if days is None:
		raise bot.Exc.ValueError(f"Period must be one of {', '.join(PERIODS)}.")
	wait = _last_run.get(ctx.qc.id, 0) + COOLDOWN_S - time.time()
	if wait > 0:
		raise bot.Exc.PermissionError(f"The tea is still brewing: /gossip can run again here in {int(wait // 60) + 1} min.")

	channels = []
	for ch in (ctx.channel, channel_2, channel_3):
		if ch is None or any(c.id == ch.id for c in channels):
			continue
		if ch.guild.id != ctx.channel.guild.id:
			raise bot.Exc.PermissionError(f"#{ch.name} is in another server.")
		if not _readable(ch, ctx.author):
			raise bot.Exc.PermissionError(f"You can't read #{ch.name}, so /gossip won't either.")
		if not _readable(ch, ch.guild.me):
			raise bot.Exc.PermissionError(f"I can't read the history of #{ch.name}.")
		channels.append(ch)
	_last_run[ctx.qc.id] = time.time()

	out = await _opted_out()
	out_names = set()
	for uid in out:
		if (m := ctx.channel.guild.get_member(uid)) is not None:
			out_names |= {n.lower() for n in (m.display_name, m.name, get_nick(m)) if n and len(n) >= 3}
	name_re = re.compile('|'.join(r'\b' + re.escape(n) + r'\b' for n in sorted(out_names, key=len, reverse=True)), re.I) \
		if out_names else None

	since = datetime.now(timezone.utc) - timedelta(days=days)
	prefix = getattr(getattr(ctx.qc, 'cfg', None), 'prefix', None) or '!'

	def is_command(m):
		text = m.content.strip()
		return bool(QUEUE_COMMAND.match(text)) or (len(text) > 1 and text.startswith(prefix) and text[1].isalpha())

	msgs, author_of = [], {}
	for ch in channels:
		n = 0
		async for m in ch.history(after=since, limit=READ_LIMIT, oldest_first=False):
			author_of[m.id] = m.author.id
			if m.author.bot or is_command(m):
				continue
			msgs.append((ch, m))
			n += 1
			if n >= MAX_MESSAGES:
				break
	msgs.sort(key=lambda x: x[1].created_at)
	msgs = msgs[-MAX_MESSAGES:]

	ids, names, kept = {}, {}, []
	for ch, m in msgs:
		if m.author.id in out:
			continue
		if m.reference and author_of.get(m.reference.message_id) in out:
			continue
		if any(u.id in out for u in m.mentions) or (name_re and name_re.search(m.clean_content)):
			continue
		names[m.author.id] = m.author.display_name
		ids[m.id] = (len(kept) + 1, names[m.author.id])
		kept.append((ch, m))

	def chat(reactors):
		per_channel, lines = {}, []
		for ch, m in kept:
			line = _line(m, ids[m.id][0], ids, names, reactors)
			if line:
				per_channel.setdefault(ch.name, []).append(line)
		for name, ls in per_channel.items():
			lines.append(f"=== #{name} ===")
			lines += ls
		text = '\n'.join(lines)
		return text[-MAX_CHARS:].split('\n', 1)[-1] if len(text) > MAX_CHARS else text

	where = ', '.join(f"#{c.name}" for c in channels)
	when = 'the last day' if days == 1 else f'the last {days} days'
	if len(kept) < 5:
		_last_run.pop(ctx.qc.id, None)
		await ctx.reply(f"🍵 Barely anything said in {where} over {when}. No tea to spill.")
		return

	try:
		# First find the quarrels, then look up who reacted only around them, then write the recap.
		text = chat({})
		try:
			spans = await find_quarrels(text)
		except Exception:  # noqa: BLE001 - the recap still works with reaction counts only
			spans = []
		if spans:
			text = chat(await _who_reacted(kept, ids, spans, out))
		recap = await generate_gossip(text, where, when)
	except Exception:
		_last_run.pop(ctx.qc.id, None)  # the AI failed: let them try again straight away
		raise
	embed = Embed(title=f"🍵 Gossip: {where}, {when}", description=(recap or "The AI had nothing to say.")[:4000],
		colour=0xc9a227)
	embed.set_footer(text=f"Read {len(kept)} messages from {len(names)} people. All in good fun: verdicts come only "
		"from what was said in chat. Don't want to be in it? /gossip_optout")
	await ctx.reply(embed=embed)
