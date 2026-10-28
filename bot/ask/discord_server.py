# -*- coding: utf-8 -*-
"""
MCP server for /ask: the Discord server's channels, messages and members, through the bot's own connection.

One is built per /ask, for the server and the member asking: only text channels both the bot and that member can
read are used, never private or restricted ones (channels @everyone can't read, and age-restricted channels), and
only the messages of members who opted in with /ask_optin are given to the AI. find_players and member_names tie Discord user ids to the people in the database (queue nicks, saved
names, in-game names and linked names), read-only through the SQL server's connection.
"""
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from nextcord import Guild, Member

from core.utils import get_nick

from bot.ask.sql_server import _read

MESSAGE_CHARS = 300      # one message is cut to this
MAX_MESSAGES = 200       # messages a tool returns at most
READ_BUDGET = 5000       # messages read from Discord per /ask, across all tools
MAX_DAYS = 30
MAX_PLAYERS = 15         # people find_players describes at most


def _key(name: str) -> str:
	""" A channel name with only its letters and digits, lowercased. """
	return ''.join(ch for ch in name.lower() if ch.isalnum())


def _readable(channel, member):
	perms = channel.permissions_for(member)
	return perms.view_channel and perms.read_message_history


def _public(channel, guild):
	""" Not private or restricted: @everyone can see the channel and read its history, and it isn't age-restricted. """
	return _readable(channel, guild.default_role) and not channel.is_nsfw()


def _user_id(text: str):
	""" The user id in '123456789012345678' or '<@123…>' / '<@!123…>', else None. """
	text = text.strip().removeprefix('<@').removeprefix('!').removesuffix('>')
	return int(text) if text.isdigit() and len(text) >= 15 else None


def _like(needle: str) -> str:
	return '%' + needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


async def _db_ids(needle: str) -> list:
	""" User ids whose name in the database contains needle: queue nicks, saved names, in-game and linked names. """
	rows, _ = await _read(
		"SELECT user_id FROM qc_players WHERE nick LIKE %(n)s "
		"UNION SELECT user_id FROM players WHERE name LIKE %(n)s "
		"UNION SELECT user_id FROM aoe_profiles WHERE user_id IS NOT NULL AND name LIKE %(n)s "
		"UNION SELECT user_id FROM aoe_name_links WHERE user_id IS NOT NULL AND name LIKE %(n)s",
		dict(n=_like(needle)), max_rows=100)
	return [r['user_id'] for r in rows if r['user_id']]


async def _players(guild, user_ids, rating_channel_id, brief=False) -> list:
	""" One line per user id with everything that names them, in Discord and in the database. """
	user_ids = [int(u) for u in user_ids]
	if not user_ids:
		return []
	where = dict(ids=user_ids, ch=rating_channel_id)
	names, ratings = {}, {}

	def add(uid, kind, value):
		if value and value not in names.setdefault(uid, {}).setdefault(kind, []):
			names[uid][kind].append(value)

	for sql, kind in (
		("SELECT user_id, nick AS v FROM qc_players WHERE user_id IN %(ids)s", 'queue nick'),
		("SELECT user_id, name AS v FROM players WHERE user_id IN %(ids)s", 'saved name'),
		("SELECT user_id, CONCAT(name, ' (profile ', profile_id, ')') AS v FROM aoe_profiles WHERE user_id IN %(ids)s",
			'in-game'),
		("SELECT user_id, name AS v FROM aoe_name_links WHERE user_id IN %(ids)s", 'linked in-game name'),
	):
		for r in (await _read(sql, where, max_rows=1000))[0]:
			add(r['user_id'], kind, r['v'])
	for r in (await _read(
		"SELECT user_id, rating, wins, losses, draws FROM qc_players WHERE channel_id = %(ch)s AND user_id IN %(ids)s",
		where, max_rows=1000))[0]:
		ratings[r['user_id']] = r

	lines = []
	for uid in dict.fromkeys(user_ids):
		m = guild.get_member(uid)
		known = names.get(uid, {})
		if m is None and not known:
			lines.append(f"{uid}: unknown (not in the server or the database)")
			continue
		parts = [f"Discord {m.display_name} (user {m.name})" if m else "not in the server any more"]
		parts += [f"{kind}: {', '.join(v[:5])}" for kind, v in known.items()]
		if r := ratings.get(uid):
			parts.append(f"rating here {r['rating']}, {r['wins']}W {r['losses']}L {r['draws']}D")
		if m and not brief:
			parts.append(f"joined {m.joined_at:%Y-%m-%d}, roles: "
				+ (', '.join(r.name for r in m.roles if not r.is_default()) or 'none'))
		lines.append(f"{uid}: " + ' | '.join(parts))
	return lines


def build_discord_server(guild: Guild, member: Member, rating_channel_id: int, opted_in: set) -> MCPServer:
	server = MCPServer("bombaybot-discord", instructions=f"Read-only access to the Discord server {guild.name}.")
	budget = [READ_BUDGET]
	left_out = [0]  # messages read but not given to the AI: their authors haven't run /ask_optin

	def channels():
		return [c for c in guild.text_channels
			if _public(c, guild) and _readable(c, member) and _readable(c, guild.me)]

	def channel(name: str):
		""" A readable channel by id or name; names match without emoji and separators (💬・qc-general = qc general). """
		name = name.strip().lstrip('#<').rstrip('>')
		readable = channels()
		key = _key(name)
		for match in (lambda c: str(c.id) == name or _key(c.name) == key, lambda c: key and key in _key(c.name)):
			found = [c for c in readable if match(c)]
			if len(found) == 1:
				return found[0]
			if found:
				raise ToolError(f"#{name} matches several channels: {', '.join('#' + c.name for c in found[:10])}.")
		raise ToolError(f"No public channel #{name} that you and I can both read (private and restricted channels are "
			"never read). Channels: "
			+ ', '.join('#' + c.name for c in readable[:60]))

	def line(m):
		text = m.clean_content.replace('\n', ' ').strip()
		if len(text) > MESSAGE_CHARS:
			text = text[:MESSAGE_CHARS] + '…'
		if m.attachments:
			text += ' [attachment]'
		reacts = ' '.join(f"{r.emoji}×{r.count}" for r in m.reactions)
		return f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {m.author.display_name} (id {m.author.id}): {text}" + \
			(f" [{reacts}]" if reacts else '')

	def note(lines, empty):
		""" The tool's answer, saying how many messages were left out because their authors didn't opt in. """
		text = '\n'.join(lines) or empty
		if left_out[0]:
			text += (f"\n({left_out[0]} messages from members who haven't opted in with /ask_optin were left out; "
				"you can't see what they said)")
			left_out[0] = 0
		return text

	async def history(c, days, match=None, limit=MAX_MESSAGES):
		"""
		The latest messages (oldest first) of a channel over the last days from members who opted in, optionally only
		the matching ones. The content of anyone else's message isn't looked at.
		"""
		if budget[0] <= 0:
			return []
		since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, MAX_DAYS)))
		found = []
		async for m in c.history(after=since, limit=budget[0], oldest_first=False):
			budget[0] -= 1
			if m.author.bot:
				continue
			if m.author.id not in opted_in:
				left_out[0] += 1
				continue
			if match is None or match(m):
				found.append(m)
				if len(found) >= limit:
					break
		return [line(m) for m in reversed(found)]

	@server.tool()
	async def list_channels() -> str:
		""" The public text channels you can read, with their category and topic. """
		return '\n'.join(
			f"#{c.name}" + (f" (in {c.category.name})" if c.category else '') + (f": {c.topic[:150]}" if c.topic else '')
			for c in channels()
		)

	@server.tool()
	async def read_messages(channel_name: str, days: int = 1, limit: int = 100) -> str:
		"""
		The latest messages of one text channel over the last days (1-30), oldest first, at most limit (up to 200).
		Only messages from members who opted in with /ask_optin; bots' messages are left out.
		"""
		if budget[0] <= 0:
			raise ToolError("Read budget for this question is used up; answer with what you have.")
		lines = await history(channel(channel_name), days, limit=max(1, min(limit, MAX_MESSAGES)))
		return note(lines, "No messages in that period.")

	@server.tool()
	async def search_messages(text: str, channel_name: str = '', days: int = 7, user_id: int = 0) -> str:
		"""
		Messages containing text (case-insensitive; empty text matches everything) over the last days (1-30), in one
		channel or, when channel_name is empty, every channel you can read. user_id keeps only that person's messages.
		Only messages from members who opted in with /ask_optin.
		"""
		needle = text.strip().lower()
		chans = [channel(channel_name)] if channel_name else channels()
		out = []
		for c in chans:
			if budget[0] <= 0:
				out.append("(read budget used up: not every channel was searched)")
				break
			lines = await history(c, days, limit=MAX_MESSAGES - len(out), match=lambda m: (
				(not needle or needle in m.clean_content.lower()) and (not user_id or m.author.id == user_id)))
			out += [f"#{c.name} {ln}" for ln in lines]
			if len(out) >= MAX_MESSAGES:
				break
		return note(out, "Nothing found.")

	@server.tool()
	async def find_players(name: str) -> str:
		"""
		People whose name contains name, matched across Discord (user name, display name, nick) and the database
		(queue nicks, saved names, in-game names from analysed games and linked in-game names), one line per Discord
		user id with all their names, rating in this channel, roles and join date. Also takes a user id or <@id>.
		Use the user id in database queries (user_id columns).
		"""
		name = name.strip()
		if (uid := _user_id(name)) is not None:
			return '\n'.join(await _players(guild, [uid], rating_channel_id)) or "No player with that id."
		needle = name.lower()
		if len(needle) < 2:
			raise ToolError("Give at least 2 characters of the name.")
		ids = [m.id for m in guild.members if not m.bot and any(
			needle in (n or '').lower() for n in (m.name, m.display_name, get_nick(m)))]
		ids += await _db_ids(needle)
		ids = list(dict.fromkeys(ids))
		lines = await _players(guild, ids[:MAX_PLAYERS], rating_channel_id)
		if len(ids) > MAX_PLAYERS:
			lines.append(f"(+{len(ids) - MAX_PLAYERS} more: give more of the name)")
		return '\n'.join(lines) or "No players found."

	@server.tool()
	async def member_names(user_ids: list[int]) -> str:
		"""
		Who user ids are (e.g. user_id values from the database), at most 100: Discord name, queue nick and in-game
		names, rating in this channel. People who left the server still get their names from the database.
		"""
		return '\n'.join(await _players(guild, user_ids[:100], rating_channel_id, brief=True))

	return server
