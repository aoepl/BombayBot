__all__ = ['aoe_link', 'aoe_unlink', 'aoe_names', 'replay_upload', 'replay_auto_off', 'replay_auto_on', 'replay_lookup_off', 'replay_lookup_on', 'aoe_strats', 'aoe_civs', 'explain_analysis', 'aoe_explain', 'aoe_export', 'aoe_seats', 'aoe_draft']

import asyncio
from io import BytesIO

from nextcord import Attachment, Member, Embed, ForumChannel, File

from core.utils import get_nick, find

import bot

MAX_UPLOAD = 40 * 1024 * 1024


async def aoe_link(ctx, name: str, player: Member = None):
	""" Link an in-game name to yourself, or (moderators) to another member. """
	target = player or ctx.author
	if target.id != ctx.author.id:
		ctx.check_perms(ctx.Perms.MODERATOR)
	owner = await bot.replays.name_owner(name)
	if owner and owner != target.id and owner != ctx.author.id:
		ctx.check_perms(ctx.Perms.MODERATOR)  # taking a name someone else linked needs a moderator
	try:
		previous = await bot.replays.link_name(name, target.id, ctx.author.id)
	except bot.replays.ReplayError as e:
		raise bot.Exc.ValueError(str(e))
	moved = f" (it was linked to <@{previous}> before)" if previous else ""
	await ctx.success(f"In-game name **{name.strip()}** is now linked to {get_nick(target)}{moved}.")


async def aoe_unlink(ctx, name: str):
	""" Remove a linked in-game name (your own, or any name for moderators). """
	owner = await bot.replays.name_owner(name)
	if owner is None:
		raise bot.Exc.NotFoundError(f"**{name.strip()}** is not linked to anyone.")
	if owner != ctx.author.id:
		ctx.check_perms(ctx.Perms.MODERATOR)
	await bot.replays.unlink_name(name)
	await ctx.success(f"Unlinked in-game name **{name.strip()}**.")


async def aoe_names(ctx, player: Member = None):
	""" Show the in-game names linked to a member. """
	target = player or ctx.author
	names = await bot.replays.linked_names(target.id)
	if not names:
		await ctx.reply(f"{get_nick(target)} has no linked in-game names. Link one with `/aoe link`.")
		return
	await ctx.reply(embed=Embed(title=f"In-game names for {get_nick(target)}", description="\n".join(names)))


async def replay_upload(ctx, file: Attachment, summary: bool = True):
	""" Analyse a recorded game file (.aoe2record or .zip). With summary off, only save the player stats. """
	if file.size > MAX_UPLOAD:
		raise bot.Exc.ValueError("That file is too large for a recorded game.")
	if not file.filename.lower().endswith(('.aoe2record', '.zip', '.gz')):
		raise bot.Exc.ValueError("Attach an .aoe2record file, or a .zip with one inside.")
	try:
		import mgz  # noqa: F401
	except ImportError:
		raise bot.Exc.ValueError("Replay analysis is not available: the mgz package is not installed on the bot.")
	data = await file.read()
	try:
		await bot.replays.analyze_upload(ctx.qc, ctx, data, file.filename, summary=summary)
	except Exception as e:
		raise bot.Exc.ValueError(f"Could not read that recorded game: {e}")


async def replay_auto_off(ctx):
	""" Stop looking up the recorded game of reported matches automatically in this channel. """
	ctx.check_perms(ctx.Perms.MODERATOR)
	await ctx.qc.cfg.update({'replay_auto': '0'})
	await ctx.success("Automatic game breakdowns are off for this channel. `/replay upload` still works; "
		"turn them back on with `/replay auto_replay_on`.")


async def replay_auto_on(ctx):
	""" Look up the recorded game of reported matches automatically again (needs a forum as the replay forum). """
	ctx.check_perms(ctx.Perms.MODERATOR)
	await ctx.qc.cfg.update({'replay_auto': '1'})
	forum = getattr(ctx.qc.cfg, 'replay_forum', None)
	note = "" if isinstance(forum, ForumChannel) else (
		" They only run with a forum channel as the replay forum: `/channel set variable:replay_forum value:#forum`.")
	await ctx.success("Automatic game breakdowns are on for this channel." + note)


async def replay_lookup_off(ctx):
	""" Stop looking up uploaded games on World's Edge: posts go out without the game links. """
	ctx.check_perms(ctx.Perms.MODERATOR)
	await ctx.qc.cfg.update({'replay_lookup': '0'})
	await ctx.success("Game lookup is off for this channel: uploaded games are posted without links to the recorded "
		"game and match pages. Turn it back on with `/replay auto_lookup_on`.")


async def replay_lookup_on(ctx):
	""" Look up uploaded games on World's Edge again to link the official download and match pages. """
	ctx.check_perms(ctx.Perms.MODERATOR)
	await ctx.qc.cfg.update({'replay_lookup': '1'})
	await ctx.success("Game lookup is on for this channel: posts link the official download and the match pages.")


async def aoe_strats(ctx, player: Member = None):
	""" A player's record from the analysed games: positions, APM, results against Elo, bests and luck. """
	from bot.replays import summaries
	target = player or ctx.author
	s = summaries.player_summary(await summaries.load_rows(ctx.qc.id), target.id)
	if not s:
		raise bot.Exc.NotFoundError(f"No analysed games for {get_nick(target)} in this channel yet. "
			"Games are added by /replay upload (and automatically after reports with a replay forum).")
	embed = Embed(title=f"Stats: {get_nick(target)}")
	for name, value in summaries.strats_lines(s):
		embed.add_field(name=name, value=value[:1024], inline=False)
	embed.set_footer(text="From the games analysed in this channel. Luck: share of a player's best position, army style "
		"and strategy (by average score) they got in each game.")
	await ctx.reply(embed=embed)


async def aoe_civs(ctx, map: str = None):
	""" Civ win rates from the analysed games, overall or on one map, with the best civs by Elo, position and map. """
	from bot.replays import summaries
	c = summaries.civ_summary(await summaries.load_rows(ctx.qc.id), map)
	if not c['games']:
		raise bot.Exc.NotFoundError("No analysed games" + (f" on {map}" if map else "") + " in this channel yet.")
	embed = Embed(title="Civ statistics" + (f": {map}" if map else ""))
	for name, value in summaries.civs_lines(c):
		embed.add_field(name=name, value=value[:1024], inline=False)
	embed.set_footer(text=f"{c['games']} analysed games in this channel. Best lists need {summaries.MIN_CIV_GAMES}+ games "
		"with a civ; win rate (games).")
	await ctx.reply(embed=embed)


async def aoe_seats(ctx, player: Member = None, map: str = None):
	""" How often each player was flank and pocket (win rate), or one player's record by map and civ. """
	from bot.replays import summaries
	stats = summaries.seat_stats(await summaries.load_rows(ctx.qc.id), map)
	where = f" on {map}" if map else ""
	if not stats:
		raise bot.Exc.NotFoundError(f"No analysed team games{where} in this channel yet.")
	if player:
		p = stats.get(player.id)
		if not p:
			raise bot.Exc.NotFoundError(f"No analysed team games{where} for {get_nick(player)} in this channel yet. "
				"Their in-game name must be linked (/aoe link).")
		embed = Embed(title=f"Flank and pocket: {get_nick(player)}{where}")
		for name, value in summaries.seats_lines(p):
			embed.add_field(name=name, value=value[:1024], inline=False)
		embed.set_footer(text="From the team games analysed in this channel: games (win rate). Maps and civs: most played.")
	else:
		names = {}
		for p in stats.values():
			if p['user_id'] and (m := ctx.channel.guild.get_member(p['user_id'])):
				names[p['user_id']] = get_nick(m)
		embed = Embed(title=f"Flank and pocket{where}", description=summaries.seats_table(stats, names)[:4096])
		embed.set_footer(text=f"Games (win rate) in each position, from {len(stats)} players' team games analysed in "
			"this channel. Most games first; unlinked in-game names shown as they are.")
	await ctx.reply(embed=embed)


async def explain_analysis(interaction, message):
	"""
	"Explain analysis" (right-click or long-press an analysis post → Apps): why each player got their scores.
	Works on the post or on any message in its thread. Answers everyone in the thread.
	"""
	try:
		title, messages = await bot.replays.explain_post(interaction.channel_id, message.id)
	except bot.replays.ReplayError as e:
		await interaction.response.send_message(str(e), ephemeral=True)
		return
	await interaction.response.send_message(content=f"**{title}**", embeds=messages[0])
	for embeds in messages[1:]:
		await interaction.followup.send(embeds=embeds)


async def aoe_explain(ctx, *args):
	""" !explain as a reply to an analysis post in a queue channel: why each player got their scores. """
	ref = getattr(ctx.message, 'reference', None)
	if not ref or not ref.message_id:
		raise bot.Exc.SyntaxError("Reply to a game analysis post with this command, or use Apps → Explain analysis on the post.")
	try:
		title, messages = await bot.replays.explain_post(ctx.message.channel.id, ref.message_id)
	except bot.replays.ReplayError as e:
		raise bot.Exc.NotFoundError(str(e))
	for i, embeds in enumerate(messages):
		await ctx.message.channel.send(content=f"**{title}**" if i == 0 else None, embeds=embeds,
			reference=ctx.message if i == 0 else None)


async def aoe_export(ctx, map: str = None, days: int = None, details: bool = False):
	""" Moderators: one JSON file of every analysed game in this channel (game cards for an AI), optionally filtered. """
	ctx.check_perms(ctx.Perms.MODERATOR)
	from bot.replays import export
	cards = await export.load_cards(ctx.qc.id, map, days, details)
	if not cards:
		raise bot.Exc.NotFoundError("No analysed games" + (f" on {map}" if map else "") +
			(f" in the last {days} days" if days else "") + " in this channel yet.")
	data, name = export.export_file(cards, getattr(ctx.channel, 'name', None), dict(map=map, days=days, details=details or None))
	size = f"{len(data) / 1024:.0f} KB" if len(data) < 1024 * 1024 else f"{len(data) / 1024 / 1024:.1f} MB"
	await ctx.reply(content=f"{len(cards)} analysed game{'s' if len(cards) != 1 else ''} ({size}). Upload the file to an AI "
		"and ask, e.g. which civs do best per map type, seat and Elo.", file=File(BytesIO(data), filename=name))


def _draft_embed(match, res, model, games, author):
	""" The draft as an embed: each team's civs (and seats and players in player mode), why each civ was picked,
	the balance and how many civs were left out. """
	from bot.replays import draft
	per_player = res['mode'] == draft.PLAYER_CIVS
	where = f"{res['map']} ({res['category']})" if res.get('category') and res['category'] != 'Other' else res['map']
	embed = Embed(title=f"Civ draft: {where}", colour=0x9a5b13)
	embed.description = ("A civ and position for each player." if per_player else
		f"{draft.TEAM_CHOICES} civs for each team: choose your players' civs from them and decide who plays where.")
	for i, team in enumerate(res['teams']):
		t = match.teams[i]
		elo = round(sum(match.ratings.get(p.id) or 0 for p in t) / len(t)) if len(t) else 0
		lines = []
		for s in team:
			if per_player:
				lines.append((f"{s.seat} · " if s.seat else "") + f"**{s.name}** `〈{s.elo}〉` → **{s.civ}**")
			else:
				lines.append(f"**{s.civ}**" + (f" · {s.seat.lower()}" if s.seat else ""))
		embed.add_field(name=f"{t.emoji} {t.name} `〈{elo}〉`", value="\n".join(lines)[:1024], inline=False)
	for i, team in enumerate(res['teams']):
		why = [f"**{s.civ}** {draft.pick_reason(model, res, i, j)}" for j, s in enumerate(team)]
		embed.add_field(name=f"Why these civs: {match.teams[i].name}", value="\n".join(why)[:1024], inline=False)
	edge = res['balance']['civ_edge']
	ahead = match.teams[0 if edge >= 0 else 1].name
	civ_line = "Civs: even." if abs(edge) < 0.005 else f"Civs: {ahead} ahead by {abs(edge):.1%}."
	out = res['excluded']
	embed.add_field(name="How the draft works", inline=False, value=(
		f"{len(out)} of the {res['civ_count']} ranked civs were left out at random first, so drafts differ from match to "
		f"match: {', '.join(out)}.\n"
		f"Teams then took the best civ left in turn, {match.teams[res['first']].name} first (coin flip), in snake order. "
		"A civ's value is how it does against Elo in this seat and on this kind of map, its matchups against the other "
		"team's civs and what taking it denies them.\n"
		f"{civ_line} Elo is not evened out with civs: the rating change already accounts for it.")[:1024])
	embed.set_footer(text=f"Drawn by {author}. From {games} analysed game{'s' if games != 1 else ''} in this channel"
		" plus the tech trees; with few games picks lean on the tech trees.")
	return embed


async def aoe_draft(ctx, mode: str = 'team', map: str = None, redraw: bool = False):
	"""
	Civ draft for your started match: civs for each team to share out (mode team), or a civ and position for every
	player (mode player). 20-35 random civs are left out first. One draft per match; moderators can redraw.
	"""
	import random
	from bot.replays import draft, insights, summaries
	match = find(lambda m: m.qc == ctx.qc and ctx.author in m.players, bot.active_matches)
	if match is None:
		raise bot.Exc.NotFoundError("You are not in a started match in this channel. /aoe draft works once your "
			"queue has started.")
	if len(match.teams[2]) or not len(match.teams[0]) or not len(match.teams[1]):
		raise bot.Exc.MatchStateError("The teams are not set yet: run /aoe draft once both teams are picked.")
	saved = getattr(match, 'civ_draft', None)
	if saved and not redraw:
		await ctx.reply(content="This match already has a civ draft (moderators can redraw it):", embed=saved)
		return
	if saved and redraw:
		ctx.check_perms(ctx.Perms.MODERATOR)
	map_name = map or (match.maps[0] if match.maps else None)
	if not map_name:
		raise bot.Exc.ValueError("This match has no map: give one with the map option.")
	rows, matchups, fights = await summaries.load_draft_data(ctx.qc.id)
	records = summaries.seat_stats(rows)
	teams = [[dict(name=get_nick(p), elo=match.ratings.get(p.id), user_id=p.id) for p in match.teams[i]] for i in (0, 1)]
	mode = draft.PLAYER_CIVS if mode == draft.PLAYER_CIVS else draft.TEAM_CIVS

	def work():  # a few seconds of number crunching: kept off the bot's event loop
		model = draft.Model(rows, matchups, fights)
		res = draft.civ_draft(model, teams, map_name, insights.map_category(map_name), mode, random.Random(), records)
		return model, res
	model, res = await asyncio.to_thread(work)
	embed = _draft_embed(match, res, model, len({r['game_key'] for r in rows}), get_nick(ctx.author))
	match.civ_draft = embed
	await ctx.reply(embed=embed)
