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
MAX_MESSAGES = 4000          # most recent messages read per run, over all channels
MAX_CHARS = 150_000          # chat sent to the AI (the most recent part, when there is more)
MESSAGE_CHARS = 400          # one message is cut to this
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


def _line(msg, num, ids, names):
	""" One message as a line for the AI: #12 [Mon 21:04] Name (replying to #9 Other): text [😂×3] """
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
	reactions = ' '.join(f"{r.emoji}×{r.count}" for r in msg.reactions)
	return (f"#{num} [{msg.created_at.strftime('%a %H:%M')}] {names[msg.author.id]}{reply}: {text}"
		+ (f" [{reactions}]" if reactions else ''))


async def gossip(ctx, period: str = '1d', channel_2: TextChannel = None, channel_3: TextChannel = None):
	from bot.bombay.ai import generate_gossip

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
	msgs = []
	for ch in channels:
		async for m in ch.history(after=since, limit=MAX_MESSAGES, oldest_first=False):
			msgs.append((ch, m))
	msgs.sort(key=lambda x: x[1].created_at)
	msgs = msgs[-MAX_MESSAGES:]

	ids, names, by_id, kept = {}, {}, {m.id: m for _, m in msgs}, []
	for ch, m in msgs:
		if m.author.bot or m.author.id in out:
			continue
		ref = by_id.get(m.reference.message_id) if m.reference else None
		if ref is not None and ref.author.id in out:
			continue
		if any(u.id in out for u in m.mentions) or (name_re and name_re.search(m.clean_content)):
			continue
		names[m.author.id] = m.author.display_name
		ids[m.id] = (len(kept) + 1, names[m.author.id])
		kept.append((ch, m))

	per_channel, lines = {}, []
	for ch, m in kept:
		line = _line(m, ids[m.id][0], ids, names)
		if line:
			per_channel.setdefault(ch.name, []).append(line)
	for name, ls in per_channel.items():
		lines.append(f"=== #{name} ===")
		lines += ls
	text = '\n'.join(lines)
	if len(text) > MAX_CHARS:
		text = text[-MAX_CHARS:].split('\n', 1)[-1]

	where = ', '.join(f"#{c.name}" for c in channels)
	when = 'the last day' if days == 1 else f'the last {days} days'
	if len(kept) < 5:
		_last_run.pop(ctx.qc.id, None)
		await ctx.reply(f"🍵 Barely anything said in {where} over {when}. No tea to spill.")
		return

	try:
		recap = await generate_gossip(text, where, when)
	except Exception:
		_last_run.pop(ctx.qc.id, None)  # the AI failed: let them try again straight away
		raise
	embed = Embed(title=f"🍵 Gossip: {where}, {when}", description=(recap or "The AI had nothing to say.")[:4000],
		colour=0xc9a227)
	embed.set_footer(text=f"Read {len(kept)} messages from {len(names)} people. All in good fun: verdicts come only "
		"from what was said in chat. Don't want to be in it? /gossip_optout")
	await ctx.reply(embed=embed)
