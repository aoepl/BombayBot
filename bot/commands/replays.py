__all__ = ['aoe_link', 'aoe_unlink', 'aoe_names', 'replay_upload']

from nextcord import Attachment, Member, Embed

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
	data = await file.read()
	try:
		await bot.replays.analyze_upload(ctx.qc, ctx, data, file.filename, summary=summary)
	except Exception as e:
		raise bot.Exc.ValueError(f"Could not read that recorded game: {e}")
