from nextcord import Embed, Colour, Streaming, Member
from core.client import dc
from core.utils import get_nick, join_and


class Embeds:
	""" This class generates discord embeds for various match states """

	def __init__(self, match):
		self.m = match
		# self.
		self.footer = dict(
			text=f"Match id: {self.m.id}",
			icon_url=dc.user.avatar.with_size(32).url if dc.user.avatar else None
			# icon_url="https://cdn.discordapp.com/avatars/240843400457355264/a51a5bf3b34d94922fd60751ba1d60ab.png?size=64"
		)

	def _ranked_nick(self, p: Member):
		if self.m.ranked:
			if self.m.qc.cfg.emoji_ranks:
				return f'{self.m.rank_str(p)}`{get_nick(p)}`'
			return f'`{self.m.rank_str(p)}{get_nick(p)}`'
		return f'`{get_nick(p)}`'

	def _ranked_mention(self, p: Member):
		if self.m.ranked:
			if self.m.qc.cfg.emoji_ranks:
				return f'{self.m.rank_str(p)}{p.mention}'
			return f'`{self.m.rank_str(p)}`{p.mention}'
		return p.mention

	def check_in(self, not_ready):
		embed = Embed(
			colour=Colour(0xf5d858),
			title=self.m.gt("__**{queue}** is now on the check-in stage!__").format(
				queue=self.m.queue.name[0].upper()+self.m.queue.name[1:]
			)
		)
		embed.add_field(
			name=self.m.gt("Waiting on:"),
			value="\n".join((f" \u200b {'❌ ' if p in self.m.check_in.discarded_players else ''}<@{p.id}>" for p in not_ready)),
			inline=False
		)
		if not len(self.m.check_in.maps):
			embed.add_field(
				name="—",
				value=self.m.gt(
					"Please react with {ready_emoji} to **check-in** or {not_ready_emoji} to **abort**!").format(
					ready_emoji=self.m.check_in.READY_EMOJI, not_ready_emoji=self.m.check_in.NOT_READY_EMOJI
				) + "\n\u200b",
				inline=False
			)
		else:
			embed.add_field(
				name="—",
				value="\n".join([
					self.m.gt("Please react with {ready_emoji} or vote for a map to **check-in**.").format(
						ready_emoji=self.m.check_in.READY_EMOJI
					),
					self.m.gt("React with {not_ready_emoji} to **abort**!").format(
						not_ready_emoji=self.m.check_in.NOT_READY_EMOJI
					) + "\n\u200b\nMaps:",
					"\n".join([
						f" \u200b \u200b {self.m.check_in.INT_EMOJIS[i]} \u200b {self.m.check_in.maps[i]}"
						for i in range(len(self.m.check_in.maps))
					])
				]),
				inline=False
			)
		embed.set_footer(**self.footer)

		return embed

	def draft(self):
		embed = Embed(
			colour=Colour(0x8758f5),
			title=self.m.gt("__**{queue}** is now on the draft stage!__").format(
				queue=self.m.queue.name[0].upper()+self.m.queue.name[1:]
			)
		)

		teams_names = [
			f"{t.emoji} \u200b **{t.name}**" +
			(f" \u200b `〈{sum((self.m.ratings[p.id] for p in t))//(len(t) or 1)}〉`" if self.m.ranked else "")
			for t in self.m.teams[:2]
		]
		team_players = [
			" \u200b ".join([
				self._ranked_nick(p) for p in t
			]) if len(t) else self.m.gt("empty")
			for t in self.m.teams[:2]
		]
		embed.add_field(name=teams_names[0], value=" \u200b ❲ \u200b " + team_players[0] + " \u200b ❳", inline=False)
		embed.add_field(name=teams_names[1], value=" \u200b ❲ \u200b " + team_players[1] + " \u200b ❳\n\u200b", inline=False)

		if len(self.m.teams[2]):
			embed.add_field(
				name=self.m.gt("Unpicked:"),
				value="\n".join((
					" \u200b " + self._ranked_nick(p)
				) for p in self.m.teams[2]),
				inline=False
			)

			if len(self.m.teams[0]) and len(self.m.teams[1]):
				msg = self.m.gt("Pick players with `/pick @player` command.")
				pick_step = len(self.m.teams[0]) + len(self.m.teams[1]) - 2
				picker_team = self.m.teams[self.m.draft.pick_order[pick_step]] if pick_step < len(self.m.draft.pick_order)-1 else None
				if picker_team:
					msg += "\n" + self.m.gt("{member}'s turn to pick!").format(member=f"<@{picker_team[0].id}>")
			else:
				msg = self.m.gt("Type {cmd} to become a captain and start picking teams.").format(
					cmd=f"`{self.m.qc.cfg.prefix}capfor {'/'.join((team.name.lower() for team in self.m.teams[:2]))}`"
				)

			embed.add_field(name="—", value=msg + "\n\u200b", inline=False)

		embed.set_footer(**self.footer)

		return embed

	def final_message(self):
		show_ranks = bool(self.m.ranked and not self.m.qc.cfg.rating_nicks)
		embed = Embed(
			colour=Colour(0x27b75e),
			title=self.m.qc.gt("__**{queue}** has started!__").format(
				queue=self.m.queue.name[0].upper()+self.m.queue.name[1:]
			)
		)

		if len(self.m.teams[0]) == 1 and len(self.m.teams[1]) == 1:  # 1v1
			p1, p2 = self.m.teams[0][0], self.m.teams[1][0]
			players = " \u200b {player1}{rating1}\n \u200b {player2}{rating2}".format(
				rating1=f" \u200b `〈{self.m.ratings[p1.id]}〉`" if show_ranks else "",
				player1=f"<@{p1.id}>",
				rating2=f" \u200b `〈{self.m.ratings[p2.id]}〉`" if show_ranks else "",
				player2=f"<@{p2.id}>",
			)
			embed.add_field(name=self.m.gt("Players"), value=players, inline=False)
		elif len(self.m.teams[0]):  # team vs team
			teams_names = [
				f"{t.emoji} \u200b **{t.name}**" +
				(f" \u200b `〈{sum((self.m.ratings[p.id] for p in t))//(len(t) or 1)}〉`" if self.m.ranked else "")
				for t in self.m.teams[:2]
			]
			team_players = [
				self.format_players(t)
				for t in self.m.teams[:2]
			]
			team_players[1] += "\n\u200b"  # Extra empty line
			embed.add_field(name=teams_names[0], value=team_players[0], inline=False)
			embed.add_field(name=teams_names[1], value=team_players[1], inline=False)
			if self.m.ranked or self.m.cfg['pick_captains']:
				embed.add_field(
					name=self.m.gt("Captains"),
					value=" \u200b " + join_and([self.m.teams[0][0].mention, self.m.teams[1][0].mention]),
					inline=False
				)

		else:  # just players list
			embed.add_field(
				name=self.m.gt("Players"),
				value=" \u200b " + " \u200b ".join((m.mention for m in self.m.players)),
				inline=False
			)
			if len(self.m.captains) and len(self.m.players) > 2:
				embed.add_field(
					name=self.m.gt("Captains"),
					value=" \u200b " + join_and([m.mention for m in self.m.captains]),
					inline=False
				)

		if len(self.m.maps):
			embed.add_field(
				name=self.m.qc.gt("Map" if len(self.m.maps) == 1 else "Maps"),
				value="\n".join((f"**{i}**" for i in self.m.maps)),
				inline=True
			)
		if self.m.cfg['server']:
			embed.add_field(name=self.m.qc.gt("Server"), value=f"`{self.m.cfg['server']}`", inline=True)

		if self.m.cfg['start_msg']:
			embed.add_field(name="—", value=self.m.cfg['start_msg'] + "\n\u200b", inline=False)

		if self.m.cfg['show_streamers']:
			if len(streamers := [p for p in self.m.players if isinstance(p.activity, Streaming)]):
				embed.add_field(name=self.m.qc.gt("Player streams"), inline=False, value="\n".join([
					f"{p.mention}: {p.activity.url}" for p in streamers
				]) + "\n\u200b")
		embed.set_footer(**self.footer)

		return embed

	async def start_predictions(self):
		from bot.bombay.ai import generate_match_summary
		from .predictions import Predictions as _P
		from core.utils import get_nick
		from core.database import db
		from core.console import log

		import asyncio
		from asyncio import gather

		team1 = self.m.teams[0]
		team2 = self.m.teams[1]
		channel_id = self.m.qc.rating.channel_id
		team1_ids = [p.id for p in team1]
		team2_ids = [p.id for p in team2]
		all_ids = team1_ids + team2_ids
		ph = lambda ids: ','.join(['%s'] * len(ids))  # noqa: E731

		maps = self.m.maps
		map_filter = " AND (" + " OR ".join(
			["FIND_IN_SET(%s, REPLACE(m.maps, '\\n', ',')) > 0"] * len(maps)
		) + ")" if maps else ""

		cords = [
			db.fetchall(
				f"SELECT user_id, wins, losses, streak FROM qc_players "
				f"WHERE channel_id = %s AND user_id IN ({ph(all_ids)})",
				[channel_id] + all_ids
			),
			db.fetchall(
				f"""
				SELECT pm1.user_id AS p1, pm2.user_id AS p2,
					COUNT(*) AS played,
					SUM(CASE WHEN m.winner = pm1.team THEN 1 ELSE 0 END) AS p1_wins
				FROM qc_player_matches pm1
				JOIN qc_player_matches pm2
					ON pm1.match_id = pm2.match_id
					AND pm1.channel_id = pm2.channel_id
					AND pm1.team != pm2.team
				JOIN qc_matches m ON pm1.match_id = m.match_id AND pm1.channel_id = m.channel_id
				WHERE pm1.channel_id = %s
					AND pm1.user_id IN ({ph(team1_ids)})
					AND pm2.user_id IN ({ph(team2_ids)})
					AND m.winner IS NOT NULL
				GROUP BY pm1.user_id, pm2.user_id
				HAVING played >= 3
				ORDER BY played DESC
				""",
				[channel_id] + team1_ids + team2_ids
			),
		]
		if maps:
			cords.append(db.fetchall(
				f"""
				SELECT pm.user_id,
					TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(m.maps, CHAR(10),
						n.n), CHAR(10), -1)) AS map_name,
					COUNT(*) AS played,
					SUM(CASE WHEN m.winner = pm.team THEN 1 ELSE 0 END) AS wins,
					SUM(CASE WHEN m.winner IS NOT NULL AND m.winner != pm.team THEN 1 ELSE 0 END) AS losses
				FROM qc_player_matches pm
				JOIN qc_matches m ON pm.match_id = m.match_id AND pm.channel_id = m.channel_id
				JOIN (SELECT 1 AS n UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5) n
					ON n.n <= 1 + LENGTH(m.maps) - LENGTH(REPLACE(m.maps, CHAR(10), ''))
				WHERE pm.channel_id = %s
					AND pm.user_id IN ({ph(all_ids)})
					AND m.winner IS NOT NULL
					{map_filter}
				GROUP BY pm.user_id, map_name
				HAVING played >= 3 AND map_name IN ({ph(maps)})
				""",
				[channel_id] + all_ids + maps * 2
			))

		results = await gather(*cords)
		player_rows, h2h_rows = results[0], results[1]
		map_rows = results[2] if maps else []

		player_stats = {r['user_id']: r for r in player_rows}
		# map_stats[user_id][map_name] = row
		map_stats: dict = {}
		for r in map_rows:
			map_stats.setdefault(r['user_id'], {})[r['map_name']] = r
		nick = {p.id: get_nick(p) for p in list(team1) + list(team2)}

		def _player_text(p):
			elo = self.m.ratings.get(p.id, "?")
			s = player_stats.get(p.id)
			if s:
				total = (s['wins'] or 0) + (s['losses'] or 0)
				wr = round(s['wins'] * 100 / total) if total else 0
				base = f"  - {get_nick(p)}: Elo {elo}, {s['wins']}W/{s['losses']}L ({wr}% wr), streak {s['streak']:+d}"
			else:
				base = f"  - {get_nick(p)}: Elo {elo}"
			if p.id in map_stats:
				map_parts = []
				for mn, mr in map_stats[p.id].items():
					map_wr = round(mr['wins'] * 100 / mr['played']) if mr['played'] else 0
					map_parts.append(f"{mn}: {mr['wins']}W/{mr['losses']}L ({map_wr}%)")
				base += f" | map form: {', '.join(map_parts)}"
			return base

		def _team_lines(team, avg_elo):
			lines = [f"Team {team.name} (Avg Elo: {avg_elo}):"]
			lines += [_player_text(p) for p in team]
			return "\n".join(lines)

		h2h_lines = []
		for r in h2h_rows[:5]:
			p1_wins, p2_wins = r['p1_wins'], r['played'] - r['p1_wins']
			h2h_lines.append(
				f"  {nick.get(r['p1'], r['p1'])} vs {nick.get(r['p2'], r['p2'])}: "
				f"{p1_wins}-{p2_wins} in {r['played']} matches"
			)

		maps_line = f"Map(s): {', '.join(self.m.maps)}\n" if self.m.maps else ""
		h2h_section = ("Head-to-head history:\n" + "\n".join(h2h_lines) + "\n") if h2h_lines else ""
		match_text = (
			f"{maps_line}"
			f"{_team_lines(team1, self.m.team_ratings[0])}\n"
			f"{_team_lines(team2, self.m.team_ratings[1])}\n"
			f"{h2h_section}"
		)

		try:
			ai_summary = await asyncio.wait_for(generate_match_summary(match_text), timeout=30)
		except asyncio.TimeoutError:
			log.warning(f"generate_match_summary timed out for match {self.m.id}")
			ai_summary = None
		except Exception as e:
			log.error(f"generate_match_summary failed for match {self.m.id}: {e}")
			ai_summary = None

		embed = Embed(
			colour=Colour(0x27b75e),
			title=self.m.qc.gt("__Predictions for Match id {match_id} has started!__").format(
				match_id=self.m.id
			)
		)
		if ai_summary:
			embed.add_field(name="Match Preview", value=ai_summary, inline=False)
		teams_display = [
			f"{_P.TEAM_EMOJIS[0]} \u200b **{team1.name}** \u200b `Avg elo: {self.m.team_ratings[0]}`",
			f"{_P.TEAM_EMOJIS[1]} \u200b **{team2.name}** \u200b `Avg elo: {self.m.team_ratings[1]}`",
		]
		embed.add_field(
			name="React to predict the winner",
			value="\n".join(teams_display),
			inline=False
		)
		embed.set_footer(**self.footer)

		return embed

	def end_predictions(self):
		embed = Embed(
			colour=Colour(0x27b75e),
			title=self.m.qc.gt("__Predictions for Match id {match_id} has ended!__").format(
				match_id=self.m.id
			)
		)

		team1 = self.m.teams[0]
		team2 = self.m.teams[1]
		votes = self.m.predictions.predictions
		team1_supporters = [u.mention for u, v in votes.items() if v == self.m.teams[0].idx]
		team2_supporters = [u.mention for u, v in votes.items() if v == self.m.teams[1].idx]

		embed.add_field(
			name=f"{team1.emoji} \u200b **Team {team1.name}** \u200b `Avg elo: {self.m.team_ratings[0]} | Odds: {round(self.m.team_odds[0], 2)}`",
			value=(" ".join(team1_supporters) and f"Supporters({len(team1_supporters)}): {chr(32).join(team1_supporters)}") or "Supporters(0): —", inline=False
		)
		embed.add_field(
			name=f"{team2.emoji} \u200b **Team {team2.name}** \u200b `Avg elo: {self.m.team_ratings[1]} | Odds: {round(self.m.team_odds[1], 2)}`",
			value=(" ".join(team2_supporters) and f"Supporters({len(team2_supporters)}): {chr(32).join(team2_supporters)}") or "Supporters(0): —", inline=False
		)
		embed.set_footer(**self.footer)

		return embed

	def format_players(self, players) -> str:
		return (" \u200b " +
		        " \u200b ".join([
					self._ranked_mention(p) for p in players
				]))
