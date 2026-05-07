# -*- coding: utf-8 -*-
import random
from collections import deque  # NEW: store failed map sets in retry order

import bot
from nextcord.errors import DiscordException

from core.utils import join_and
from core.console import log


class CheckIn:

	READY_EMOJI = "☑"
	NOT_READY_EMOJI = "⛔"
	INT_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]

	def __init__(self, match, timeout):
		self.m = match
		self.timeout = timeout
		self.allow_discard = self.m.cfg['check_in_discard']
		self.discard_immediately = self.m.cfg['check_in_discard_immediately']
		self.ready_players = set()
		self.discarded_players = set()
		self.message = None
		self.finished = False

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			self.ready_players.add(p)

		if len(self.m.cfg['maps']) > 1 and self.m.cfg['vote_maps']:
			self.maps = self._take_maps_for_attempt()  # CHANGED: reuse failed maps before generating new ones
			self.map_votes = [set() for i in self.maps]
		else:
			self.maps = []
			self.map_votes = []

		if self.timeout:
			self.m.states.append(self.m.CHECK_IN)

	# NEW: get queue-level retry storage for failed map sets
	def _get_failed_map_retries(self):
		retries = getattr(self.m.queue, 'failed_map_retries', None)
		if retries is None:
			retries = deque()
			setattr(self.m.queue, 'failed_map_retries', retries)
		return retries

	# NEW: use oldest failed map set first, otherwise generate fresh maps
	def _take_maps_for_attempt(self):
		retries = self._get_failed_map_retries()
		if retries:
			return list(retries.popleft())

		return self.m.random_maps(
			self.m.cfg['maps'],
			self.m.cfg['vote_maps'],
			self.m.queue.last_maps
		)

	# NEW: preserve this attempt's maps when match creation fails
	def _store_maps_for_retry(self):
		if not self.maps:
			return

		retries = self._get_failed_map_retries()
		retries.append(list(self.maps))

	async def think(self, frame_time):
		if not self.finished and frame_time > self.m.start_time + self.timeout:
			ctx = bot.SystemContext(self.m.qc)
			if self.allow_discard:
				await self.abort_timeout(ctx)
			else:
				await self.finish(ctx)

	async def start(self, ctx):
		not_ready = list(filter(lambda m: m not in self.ready_players, self.m.players))
		self.message = await ctx.channel.send(embed=self.m.embeds.check_in(not_ready))

		emojis = [self.READY_EMOJI, '🔸', self.NOT_READY_EMOJI] if self.allow_discard else [self.READY_EMOJI]
		emojis += [self.INT_EMOJIS[n] for n in range(len(self.maps))]
		try:
			for emoji in emojis:
				await self.message.add_reaction(emoji)
		except DiscordException:
			pass
		bot.waiting_reactions[self.message.id] = self.process_reaction

	async def refresh(self, ctx):
		not_ready = list(filter(lambda m: m not in self.ready_players, self.m.players))

		if len(self.discarded_players) and len(self.discarded_players) == len(not_ready):
			if self.message:
				bot.waiting_reactions.pop(self.message.id, None)
				try:
					await self.message.delete()
				except DiscordException:
					pass

			self._store_maps_for_retry()  # NEW: keep same maps for next trigger after failed check-in

			# all not ready players discarded check in
			await ctx.notice('\n'.join((
				self.m.gt("{member} has aborted the check-in.").format(
					member=', '.join([m.mention for m in self.discarded_players])
				),
				self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
			)))

			self.m.aborted = True  # Mark match as aborted
			bot.active_matches.remove(self.m)
			await self.m.queue.revert(
				ctx,
				list(self.discarded_players),
				[m for m in self.m.players if m not in self.discarded_players]
			)
			return

		if len(not_ready):
			try:
				await self.message.edit(content=None, embed=self.m.embeds.check_in(not_ready))
			except DiscordException:
				pass
		else:
			await self.finish(ctx)

	async def finish(self, ctx):
		self.finished = True
		if self.message:
			bot.waiting_reactions.pop(self.message.id, None)
			try:
				await self.message.delete()
			except DiscordException:
				pass
		self.ready_players = set()
		if len(self.maps):
			order = list(range(len(self.maps)))
			random.shuffle(order)
			order.sort(key=lambda n: len(self.map_votes[n]), reverse=True)
			self.m.maps = [self.maps[n] for n in order[:self.m.cfg['map_count']]]

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			bot.auto_ready.pop(p.id)

		await self.m.next_state(ctx)

	async def process_reaction(self, reaction, user, remove=False):
		if self.m.state != self.m.CHECK_IN or user not in self.m.players:
			return

		if str(reaction) in self.INT_EMOJIS:
			idx = self.INT_EMOJIS.index(str(reaction))
			if idx < len(self.maps):  # CHANGED: fix map index bounds check
				if remove:
					self.map_votes[idx].discard(user.id)
					self.ready_players.discard(user)
				else:
					self.map_votes[idx].add(user.id)
					self.discarded_players.discard(user)
					self.ready_players.add(user)
				await self.refresh(bot.SystemContext(self.m.qc))

		elif str(reaction) == self.READY_EMOJI:
			if remove:
				self.ready_players.discard(user)
			else:
				self.discarded_players.discard(user)
				self.ready_players.add(user)
			await self.refresh(bot.SystemContext(self.m.qc))

		elif str(reaction) == self.NOT_READY_EMOJI and self.allow_discard:
			if self.discard_immediately:
				return await self.abort_member(bot.SystemContext(self.m.qc), user)
			return await self.discard_member(bot.SystemContext(self.m.qc), user)

	async def set_ready(self, ctx, member, ready):
		if self.m.state != self.m.CHECK_IN:
			raise bot.Exc.MatchStateError(self.m.gt("The match is not on the check-in stage."))
		if ready:
			self.ready_players.add(member)
			self.discarded_players.discard(member)
			await self.refresh(ctx)
		elif not ready:
			if not self.allow_discard:
				raise bot.Exc.PermissionError(self.m.gt("Discarding check-in is not allowed."))
			if self.discard_immediately:
				return await self.abort_member(ctx, member)
			return await self.discard_member(ctx, member)

	async def discard_member(self, ctx, member):
		self.ready_players.discard(member)
		self.discarded_players.add(member)
		await self.refresh(ctx)

	async def abort_member(self, ctx, member):
		bot.waiting_reactions.pop(self.message.id, None)
		self._store_maps_for_retry()  # NEW: keep same maps when player abandons
		await self.message.delete()
		await ctx.notice("\n".join((
			self.m.gt("{member} has aborted the check-in.").format(member=f"<@{member.id}>"),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))

		self.m.aborted = True  # Mark match as aborted
		bot.active_matches.remove(self.m)
		await self.m.queue.revert(ctx, [member], [m for m in self.m.players if m != member])

	async def abort_timeout(self, ctx):
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if self.message:
			bot.waiting_reactions.pop(self.message.id, None)
			try:
				await self.message.delete()
			except DiscordException:
				pass

		self._store_maps_for_retry()  # NEW: keep same maps when check-in times out

		self.m.aborted = True  # Mark match as aborted
		bot.active_matches.remove(self.m)

		await ctx.notice("\n".join((
			self.m.gt("{members} was not ready in time.").format(members=join_and([m.mention for m in not_ready])),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))

		await self.m.queue.revert(ctx, not_ready, list(self.ready_players))