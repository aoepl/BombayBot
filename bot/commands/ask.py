# -*- coding: utf-8 -*-
"""
/ask: a question in plain words, answered by an AI agent that looks things up itself through two MCP servers:
the bot's database (read-only SQL: ratings, matches, analysed games ...) and this Discord server (channels, messages,
members). Only channels both the bot and the person asking can read are used.
"""
__all__ = ['ask']

import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from nextcord import Embed

import bot

COOLDOWN_S = 60          # per member
QUESTION_CHARS = 500
_last_run = {}


def _instructions(ctx) -> str:
	now = datetime.now(timezone.utc)
	return (
		"You answer questions from members of an Age of Empires II pickup games Discord community about their games "
		"and their server. Look the answer up with your tools; never guess numbers or invent names, messages or games.\n"
		"Tools: list_tables and query read the bot's MySQL or MariaDB database (read-only; list_tables says which). list_channels, read_messages, "
		"search_messages, find_players and member_names read the Discord server.\n"
		"Database notes: call list_tables first to see the columns. Times ('at', 'last_ranked_match_at' ...) are unix "
		"seconds (UTC). Most tables have a channel_id: the queue channel the question is asked in is "
		f"{ctx.qc.id} and its ratings (qc_players, qc_rating_history) are kept under channel_id {ctx.qc.rating.channel_id}; "
		"keep to those unless the question says otherwise. qc_players holds each player's rating, wins and losses; "
		"qc_matches and qc_player_matches the pickup matches and who played in them (team 0 is alpha, 1 is beta); "
		"aoe_games and aoe_game_players the analysed recorded games (civ, seat, won, elo, strategy ...). "
		"user_id values are Discord user ids. To find a person, call find_players with their name first: it matches Discord "
		"names, queue nicks and in-game names and gives their user id; use that id in your queries, never a name. Use "
		"member_names to turn user ids from your query results into names. "
		"Aggregate in SQL instead of reading many rows.\n"
		f"The person asking is {ctx.author.display_name} (id {ctx.author.id}); 'me' and 'I' mean them. "
		f"Now is {now:%Y-%m-%d %H:%M} UTC (unix {int(now.timestamp())}); the community is mostly in India (IST, UTC+5:30).\n"
		"Answer in Discord markdown, at most 250 words, with the numbers you found. Say plainly when the data doesn't "
		"have the answer. Be quirky and ruthless in answers. You may quote insults or slurs from the chat."
	)


async def ask(ctx, question: str):
	from mcp import Client
	from bot.ask.discord_server import build_discord_server
	from bot.ask.sql_server import build_sql_server
	from bot.bombay.ai import run_agent

	question = question.strip()[:QUESTION_CHARS]
	if not question:
		raise bot.Exc.ValueError("Ask a question.")
	wait = _last_run.get(ctx.author.id, 0) + COOLDOWN_S - time.time()
	if wait > 0:
		raise bot.Exc.PermissionError(f"You can /ask again in {int(wait) + 1} s.")
	_last_run[ctx.author.id] = time.time()

	guild = ctx.channel.guild
	try:
		async with AsyncExitStack() as stack:
			clients = [
				await stack.enter_async_context(Client(build_sql_server())),
				await stack.enter_async_context(Client(build_discord_server(guild, ctx.author, ctx.qc.rating.channel_id))),
			]
			answer, used = await run_agent(question, _instructions(ctx), clients)
	except Exception:
		_last_run.pop(ctx.author.id, None)  # the AI failed: let them try again straight away
		raise

	queries = sum(1 for name in used if name in ('list_tables', 'query'))
	lookups = len(used) - queries
	embed = Embed(title=question[:250], description=(answer or "The AI couldn't find an answer.")[:4000], colour=0x3b88c3)
	embed.set_footer(text=f"AI answer from {queries} database and {lookups} Discord lookups, asked by "
		f"{ctx.author.display_name}. It can be wrong.")
	await ctx.reply(embed=embed)
