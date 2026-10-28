# -*- coding: utf-8 -*-
"""
/ask: a question in plain words, answered by an AI agent that looks things up itself through two MCP servers:
the bot's database (read-only SQL: ratings, matches, analysed games ...) and this Discord server (channels, messages,
members). Only public channels both the bot and the person asking can read are used: never private or restricted
ones (channels @everyone can't read, and age-restricted channels).

Opt-in: only members who ran /ask_optin (and confirmed the risks it lists) can use /ask, and the AI only gets the
Discord messages of opted-in members (run /ask_optin again to opt out). Everyone else's messages are dropped on their
author id alone, never looked at or passed on; their stats in the database and their names are still used.
"""
__all__ = ['ask', 'ask_optin']

import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from nextcord import Embed

from core.database import db

import bot

COOLDOWN_S = 30          # per member
QUESTION_CHARS = 500
_last_run = {}

db.ensure_table(dict(
	tname="ask_optin",
	columns=[
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="at", ctype=db.types.int),
	],
	primary_keys=["user_id"]
))


async def _opted_in():
	return {r['user_id'] for r in await db.select(('user_id',), 'ask_optin')}


CONFIRM = 'I understand the risks'  # also the only choice of /ask_optin's confirm option

RISKS = (
	"**Before you opt in to /ask, know what it means:**\n"
	"⚠️ **Your messages can be read.** Whenever anyone runs /ask, the AI can read your messages from the last 30 days "
	"in the server's public channels (never private or age-restricted ones).\n"
	"⚠️ **They leave Discord.** Messages the AI reads are sent to Google (the Gemini API) to write the answer. How "
	"Google keeps or uses them depends on the bot's API plan, and on a free plan Google may use them to improve its "
	"products.\n"
	"⚠️ **Answers are public.** The answer is posted in the channel for everyone to see. Anyone in the server can ask "
	"about you, and the AI can quote you out of context or get you wrong.\n"
	"⚠️ **It can be harsh.** /ask is set to be quirky and ruthless, and it may repeat insults or slurs from the chat, "
	"including yours.\n"
	"⚠️ **Opting out doesn't undo answers.** Opting out stops /ask reading your messages from then on, but answers "
	"already posted stay.\n"
	"ℹ️ /ask only works for members who opted in. Your stats, ratings and names in the bot's database are used either "
	"way."
)


async def ask_optin(ctx, confirm: str = None):
	"""
	Opt in to /ask (only after confirming the risks), or out again: opted-in members can use /ask, and it may read
	their Discord messages.
	"""
	if ctx.author.id in await _opted_in():
		if confirm == CONFIRM:
			await ctx.reply_dm("You're already opted in. Run /ask_optin without confirm to opt out.")
			return
		await db.delete('ask_optin', where=dict(user_id=ctx.author.id))
		await ctx.reply_dm("You're opted out: /ask won't read your Discord messages any more, and you can't use "
			"/ask until you opt in again. Answers already posted stay. Your stats and name can still be used.")
	elif confirm != CONFIRM:
		await ctx.reply_dm(embed=Embed(title="Opt in to /ask?", colour=0xe67e22, description=RISKS + "\n\n"
			f"To opt in, run **/ask_optin** again and pick **confirm: {CONFIRM}**. Run /ask_optin at any time later to "
			"opt out."))
	else:
		await db.insert('ask_optin', dict(user_id=ctx.author.id, at=int(time.time())), on_dublicate='replace')
		await ctx.reply_dm("You're opted in: you can use /ask, and it can read your messages in the server's public "
			"channels, send them to Google's Gemini API and quote you in public answers. Run /ask_optin again to opt "
			"out.")


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
		"Discord messages are opt-in: you only see the messages of members who ran /ask_optin, and nothing at all of "
		"anyone else's. Never guess what other people said; when the answer needs them, say only members who opted in "
		"with /ask_optin can be read.\n"
		"Answer in Discord markdown, at most 250 words, with the numbers you found. Say plainly when the data doesn't "
		"have the answer. Be quirky and ruthless in answers. You may quote insults or slurs from the chat."
	)


async def ask(ctx, question: str):
	from mcp import Client
	from bot.ask.discord_server import build_discord_server
	from bot.ask.sql_server import build_sql_server
	from bot.bombay.ai import run_agent

	opted_in = await _opted_in()
	if ctx.author.id not in opted_in:
		raise bot.Exc.PermissionError("/ask is only for members who opted in, as it lets the AI read their Discord "
			"messages. Run /ask_optin to see the risks and opt in.")
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
				await stack.enter_async_context(Client(build_discord_server(guild, ctx.author, ctx.qc.rating.channel_id, opted_in))),
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
