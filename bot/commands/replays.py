__all__ = ['replay_upload']

from nextcord import Attachment

import bot

MAX_UPLOAD = 40 * 1024 * 1024


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
