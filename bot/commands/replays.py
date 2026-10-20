__all__ = ['aoe_link', 'aoe_set', 'aoe_unlink', 'aoe_profile', 'replay_match', 'replay_upload']

from nextcord import Member, Attachment, Embed

from core.utils import get_nick

import bot

MAX_UPLOAD = 40 * 1024 * 1024


def _profile_url(profile_id):
	return f"https://www.aoe2insights.com/user/{profile_id}/"


async def aoe_link(ctx, identifier: str):
	""" Link your Discord account to your AoE2 profile. """
	try:
		profile_id, info = await bot.replays.link(ctx.author.id, identifier)
	except (bot.replays.ReplayError, bot.replays.WorldsEdgeError) as e:
		raise bot.Exc.ValueError(str(e))
	rating = f", team rating {info['tg_rating']}" if info.get('tg_rating') else ""
	await ctx.success(f"Linked to **{info.get('alias')}** (profile {profile_id}{rating}).\n{_profile_url(profile_id)}")


async def aoe_set(ctx, player: Member, identifier: str):
	""" Moderators: link another member's AoE2 profile. """
	ctx.check_perms(ctx.Perms.MODERATOR)
	try:
		profile_id, info = await bot.replays.link(player.id, identifier)
	except (bot.replays.ReplayError, bot.replays.WorldsEdgeError) as e:
		raise bot.Exc.ValueError(str(e))
	await ctx.success(f"Linked {get_nick(player)} to **{info.get('alias')}** (profile {profile_id}).")


async def aoe_unlink(ctx, player: Member = None):
	if player and player.id != ctx.author.id:
		ctx.check_perms(ctx.Perms.MODERATOR)
	target = player or ctx.author
	await bot.replays.unlink(target.id)
	await ctx.success(f"Removed the AoE2 link for {get_nick(target)}.")


async def aoe_profile(ctx, player: Member = None):
	target = player or ctx.author
	row = await bot.replays.get_link(target.id)
	if not row:
		raise bot.Exc.NotFoundError(f"{get_nick(target)} has not linked an AoE2 account. Use `/aoe link`.")
	await ctx.reply(embed=Embed(
		title=f"{get_nick(target)} → {row['alias'] or row['profile_id']}",
		description=f"Profile {row['profile_id']}\n{_profile_url(row['profile_id'])}"
	))


async def replay_match(ctx, match_id: int):
	""" Moderators: find, analyse and post the recorded game for an already reported match. """
	ctx.check_perms(ctx.Perms.MODERATOR)
	if not ctx.qc.cfg.replay_forum:
		raise bot.Exc.ValueError("Set a replay forum first: `/channel set variable:replay_forum value:#your-forum`.")
	try:
		await bot.replays.schedule_from_history(ctx.qc, match_id)
	except (bot.replays.ReplayError, bot.replays.WorldsEdgeError) as e:
		raise bot.Exc.NotFoundError(str(e))
	await ctx.success(f"Posted the breakdown for match ({match_id}).")


async def replay_upload(ctx, file: Attachment, summary: bool = True):
	""" Analyse a recorded game file (.aoe2record or .zip). With summary off, only save the player stats. """
	if file.size > MAX_UPLOAD:
		raise bot.Exc.ValueError("That file is too large for a recorded game.")
	if not file.filename.lower().endswith(('.aoe2record', '.zip', '.gz')):
		raise bot.Exc.ValueError("Attach an .aoe2record file, or a .zip with one inside.")
	data = await file.read()
	try:
		await bot.replays.analyze_upload(ctx.qc, ctx, data, file.filename, summary=summary)
	except Exception as e:
		raise bot.Exc.ValueError(f"Could not read that recorded game: {e}")
