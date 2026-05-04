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
		dict(cname="win_prob", ctype=db.types.float),
		dict(cname="at", ctype=db.types.int)
	],
	primary_keys=["id"]
))

class Predictions:

	TEAM_EMOJIS = ["🔵", "🔴"]

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
			self.message = await ctx.channel.send(embed=await self.embeds.start_predictions())
			await self.message.add_reaction(self.TEAM_EMOJIS[0])
			await self.message.add_reaction(self.TEAM_EMOJIS[1])
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
		odds = self.m.team_odds  # [odds_team0, odds_team1]; win_prob = 1/odds
		await db.insert_many('predictions', [
			dict(
				guild_id=guild_id,
				user_id=user.id,
				match_id=self.m.id,
				team=team_vote,
				win_prob=(
					round(100.0 / odds[team_vote], 2)
					if odds and odds[0] and odds[1] and team_vote in [0,1]
					else 50.0
				),
				at=now
			)
			for user, team_vote in self.predictions.items()
		], on_dublicate="ignore")

	async def process_reaction(self, reaction, user):
		if self.m.state != self.m.WAITING_REPORT or user in self.m.players:
			return

		if str(reaction) == self.TEAM_EMOJIS[0]:
			try:
				await self.message.remove_reaction(self.TEAM_EMOJIS[1], user)
			except DiscordException:
				pass
			self.predictions[user] = self.m.teams[0].idx
		elif str(reaction) == self.TEAM_EMOJIS[1]:
			try:
				await self.message.remove_reaction(self.TEAM_EMOJIS[0], user)
			except DiscordException:
				pass
			self.predictions[user] = self.m.teams[1].idx
