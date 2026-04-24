# -*- coding: utf-8 -*-
import time
import bot
from nextcord.errors import DiscordException

from .embeds import Embeds
from core.console import log
from core.database import db

db.ensure_table(dict(
	tname="predictions",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="guild_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="team", ctype=db.types.bool),
		dict(cname="at", ctype=db.types.int)
	],
	primary_keys=["id"]
))

class Predictions:

	TEAM1_EMOJI = "1️⃣"
	TEAM2_EMOJI = "2️⃣"

	TEAM1_ID = 0
	TEAM2_ID = 1

	def __init__(self, match, timeout):
		self.m = match
		self.timeout = timeout
		self.predictions = dict()
		self.embeds = Embeds(self.m)  # pass Match object, not self
		self.message = None
		self.finished = False

	async def think(self, frame_time):
		if not self.finished and self.message and frame_time > self.m.start_time + self.timeout:
			ctx = bot.SystemContext(self.m.qc)
			await self.finish(ctx)

	async def start(self, ctx):
		try:
			self.message = await ctx.channel.send(embed=self.embeds.start_predictions())
			emojis = [self.TEAM1_EMOJI, self.TEAM2_EMOJI]
			for emoji in emojis:
				await self.message.add_reaction(emoji)
			bot.waiting_reactions[self.message.id] = self.process_reaction
		except DiscordException as e:
			log.error(f"Predictions.start failed: {e}")

	async def finish(self, ctx):
		self.finished = True
		if self.message:
			bot.waiting_reactions.pop(self.message.id, None)
			try:
				await self.message.delete()
			except DiscordException:
				pass
		await ctx.notice(embed=self.embeds.end_predictions())
		await self._save_predictions()

	async def _save_predictions(self):
		if not self.predictions:
			return
		guild_id = self.m.qc.guild_id
		now = int(time.time())
		await db.insert_many('predictions', [
			dict(guild_id=guild_id, user_id=user.id, match_id=self.m.id, team=team_vote, at=now)
			for user, team_vote in self.predictions.items()
		], on_dublicate="ignore")

	async def process_reaction(self, reaction, user):
		if self.m.state != self.m.WAITING_REPORT or user in self.m.players:
			return

		if str(reaction) == self.TEAM1_EMOJI:
			try:
				await self.message.remove_reaction(self.TEAM2_EMOJI, user)
			except DiscordException:
				pass
			self.predictions[user] = self.TEAM1_ID
		elif str(reaction) == self.TEAM2_EMOJI:
			try:
				await self.message.remove_reaction(self.TEAM1_EMOJI, user)
			except DiscordException:
				pass
			self.predictions[user] = self.TEAM2_ID
