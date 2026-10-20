__all__ = ['aoe_link', 'aoe_unlink', 'aoe_names', 'replay_upload', 'replay_auto_off', 'replay_auto_on', 'replay_lookup_off', 'replay_lookup_on', 'aoe_strats', 'aoe_civs']

from nextcord import Attachment, Member, Embed, ForumChannel

from core.utils import get_nick

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
	embed = Embed(title=f"Strats: {get_nick(target)}")
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
