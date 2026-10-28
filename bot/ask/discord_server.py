# -*- coding: utf-8 -*-
"""
MCP server for /ask: the Discord server's channels, messages and members, through the bot's own connection.

One is built per /ask, for the server and the member asking: only text channels both the bot and that member can
read are used.
"""
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from nextcord import Guild, Member

from core.utils import get_nick

MESSAGE_CHARS = 300      # one message is cut to this
MAX_MESSAGES = 200       # messages a tool returns at most
READ_BUDGET = 5000       # messages read from Discord per /ask, across all tools
MAX_DAYS = 30


def _readable(channel, member):
	perms = channel.permissions_for(member)
	return perms.view_channel and perms.read_message_history


def build_discord_server(guild: Guild, member: Member) -> MCPServer:
	server = MCPServer("bombaybot-discord", instructions=f"Read-only access to the Discord server {guild.name}.")
	budget = [READ_BUDGET]

	def channels():
		return [c for c in guild.text_channels if _readable(c, member) and _readable(c, guild.me)]

	def channel(name: str):
		name = name.strip().lstrip('#').lower()
		for c in channels():
			if c.name.lower() == name or str(c.id) == name:
				return c
		raise ToolError(f"No channel #{name} that you and I can both read; use list_channels.")

	def line(m):
		text = m.clean_content.replace('\n', ' ').strip()
		if len(text) > MESSAGE_CHARS:
			text = text[:MESSAGE_CHARS] + '…'
		if m.attachments:
			text += ' [attachment]'
		reacts = ' '.join(f"{r.emoji}×{r.count}" for r in m.reactions)
		return f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {m.author.display_name} (id {m.author.id}): {text}" + \
			(f" [{reacts}]" if reacts else '')

	async def history(c, days, match=None, limit=MAX_MESSAGES):
		""" The latest messages (oldest first) of a channel over the last days, optionally only the matching ones. """
		if budget[0] <= 0:
			return []
		since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, MAX_DAYS)))
		found = []
		async for m in c.history(after=since, limit=budget[0], oldest_first=False):
			budget[0] -= 1
			if not m.author.bot and (match is None or match(m)):
				found.append(m)
				if len(found) >= limit:
					break
		return [line(m) for m in reversed(found)]

	@server.tool()
	async def list_channels() -> str:
		""" The text channels you can read, with their category and topic. """
		return '\n'.join(
			f"#{c.name}" + (f" (in {c.category.name})" if c.category else '') + (f": {c.topic[:150]}" if c.topic else '')
			for c in channels()
		)

	@server.tool()
	async def read_messages(channel_name: str, days: int = 1, limit: int = 100) -> str:
		"""
		The latest messages of one text channel over the last days (1-30), oldest first, at most limit (up to 200).
		Bots' messages are left out.
		"""
		if budget[0] <= 0:
			raise ToolError("Read budget for this question is used up; answer with what you have.")
		lines = await history(channel(channel_name), days, limit=max(1, min(limit, MAX_MESSAGES)))
		return '\n'.join(lines) or "No messages in that period."

	@server.tool()
	async def search_messages(text: str, channel_name: str = '', days: int = 7, user_id: int = 0) -> str:
		"""
		Messages containing text (case-insensitive; empty text matches everything) over the last days (1-30), in one
		channel or, when channel_name is empty, every channel you can read. user_id keeps only that person's messages.
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
		return '\n'.join(out) or "Nothing found."

	@server.tool()
	async def find_members(name: str) -> str:
		""" Members whose name, display name or nick contains name, with their user id, roles and join date. """
		needle = name.strip().lower()
		found = [m for m in guild.members if not m.bot and any(
			needle in (n or '').lower() for n in (m.name, m.display_name, get_nick(m)))][:25]
		return '\n'.join(
			f"{m.display_name} (user {m.name}, id {m.id}), joined {m.joined_at:%Y-%m-%d}, roles: "
			+ (', '.join(r.name for r in m.roles if not r.is_default()) or 'none')
			for m in found
		) or "No members found."

	@server.tool()
	async def member_names(user_ids: list[int]) -> str:
		""" The current display names for user ids (e.g. user_id values from the database), at most 100. """
		lines = []
		for uid in user_ids[:100]:
			m = guild.get_member(uid)
			name = m.display_name if m else 'not in the server'
			lines.append(f"{uid}: {name}")
		return '\n'.join(lines)

	return server
