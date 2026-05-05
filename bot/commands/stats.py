__all__ = ['last_game', 'stats', 'bombayai', 'top', 'rank', 'leaderboard']

import datetime
from time import time
from math import ceil
from nextcord import Member, Embed, Colour

from core.utils import get, find, seconds_to_str, get_nick, discord_table
from core.database import db

import bot


async def last_game(ctx, queue: str = None, player: Member = None, match_id: int = None):
	lg = None

	if match_id:
		lg = await db.select_one(
			['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, match_id=match_id), order_by="match_id", limit=1
		)

	elif queue:
		if queue := find(lambda q: q.name.lower() == queue.lower(), ctx.qc.queues):
			lg = await db.select_one(
				['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, queue_id=queue.id), order_by="match_id", limit=1
			)

	elif player and (member := await ctx.get_member(player)) is not None:
		if match := await db.select_one(
			['match_id'], "qc_player_matches", where=dict(channel_id=ctx.qc.id, user_id=member.id),
			order_by="match_id", limit=1
		):
			lg = await db.select_one(
				['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, match_id=match['match_id'])
			)

	else:
		lg = await db.select_one(
			['*'], "qc_matches", where=dict(channel_id=ctx.qc.id), order_by="match_id", limit=1
		)

	if not lg:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Nothing found"))

	players = await db.select(
		['user_id', 'nick', 'team'], "qc_player_matches",
		where=dict(match_id=lg['match_id'])
	)
	embed = Embed(colour=Colour(0x50e3c2))
	embed.add_field(name=lg['queue_name'], value=seconds_to_str(int(time()) - lg['at']) + " ago")
	if len(team := [p['nick'] for p in players if p['team'] == 0]):
		embed.add_field(name=lg['alpha_name'], value="`" + ", ".join(team) + "`")
	if len(team := [p['nick'] for p in players if p['team'] == 1]):
		embed.add_field(name=lg['beta_name'], value="`" + ", ".join(team) + "`")
	if len(team := [p['nick'] for p in players if p['team'] is None]):
		embed.add_field(name=ctx.qc.gt("Players"), value="`" + ", ".join(team) + "`")
	if lg['ranked']:
		if lg['winner'] is None:
			winner = ctx.qc.gt('Draw')
		else:
			winner = [lg['alpha_name'], lg['beta_name']][lg['winner']]
		embed.add_field(name=ctx.qc.gt("Winner"), value=winner)
	await ctx.reply(embed=embed)


async def stats(ctx, player: Member = None, period: str = None):
	_period_days = {'1M': 30, '6M': 180, '1Y': 365}
	days = _period_days.get(period) if period else None
	ts_from = int(time()) - days * 86400 if days else None

	if player:
		if (member := await ctx.get_member(player)) is not None:
			data = await bot.stats.user_stats(ctx.qc.id, member.id, ts_from=ts_from, guild_id=ctx.qc.guild_id)
			target = get_nick(member)
		else:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	else:
		data = await bot.stats.qc_stats(ctx.qc.id, ts_from=ts_from)
		target = f"#{ctx.channel.name}"

	period_label = f" ({period})" if period else ""
	embed = Embed(
		title=ctx.qc.gt("Stats for __{target}__").format(target=target) + period_label,
		colour=Colour(0x50e3c2),
		description=ctx.qc.gt("**Total matches: {count}**").format(count=data['total'])
	)
	for q in data['queues']:
		embed.add_field(name=q['queue_name'], value=str(q['count']), inline=True)
	ratings = data.get('ratings')
	if ratings:
		def _fmt_at(val):
			if val is None:
				return 'N/A'
			if isinstance(val, datetime.datetime):
				return val.strftime('%Y-%m-%d')
			return datetime.datetime.fromtimestamp(int(val)).strftime('%Y-%m-%d')

		embed.add_field(name='Result', value=f"Win: {str(ratings['wins'])} | Loss: {str(ratings['losses'])} | Winrate: {str(ratings['win_pct'])}%", inline=False)
		embed.add_field(name='Rating', value=f"Current: **{ratings['rating']}** | Max: **{ratings['max_rating']}** ({_fmt_at(ratings['max_rating_at'])}) | Min: **{ratings['min_rating']}** ({_fmt_at(ratings['min_rating_at'])})", inline=False)
		embed.add_field(name='Streak', value=f"Current: {str(ratings['current_streak'])} | Max Win: {str(ratings['max_win_streak'])} | Max Loss: {str(ratings['max_loss_streak'])}", inline=False)

	maps = data.get('maps')
	if maps:
		maps_data = "```markdown\n# Map | Played | Won | Lost | Winrate\n"
		for m in data['maps'][:5]:
			maps_data += f"{m['map_name']} | {m['played']} | {m['wins']} | {m['losses']} | {m['win_pct']}%\n"
		maps_data += "```"
		embed.add_field(name='Maps', value=maps_data, inline=False)

	best_ally = data.get('best_ally')
	if best_ally:
		best_ally_data = "```markdown\n# Ally | Played | Won | Lost | Winrate\n"
		for ba in data['best_ally'][:5]:
			best_ally_data += f"{ba['nick']} | {ba['played']} | {ba['wins']} | {ba['losses']} | {ba['weighted_win_pct']}%\n"
		best_ally_data += "```"
		embed.add_field(name='Best Ally (Hard carry)', value=best_ally_data, inline=False)

	worst_ally = data.get('worst_ally')
	if worst_ally:
		worst_ally_data = "```markdown\n# Ally | Played | Won | Lost | Winrate\n"
		for wa in data['worst_ally'][:5]:
			worst_ally_data += f"{wa['nick']} | {wa['played']} | {wa['wins']} | {wa['losses']} | {wa['weighted_win_pct']}%\n"
		worst_ally_data += "```"
		embed.add_field(name='Worst Ally (Dead weight)', value=worst_ally_data, inline=False)

	best_enemy = data.get('best_enemy')
	if best_enemy:
		best_enemy_data = "```markdown\n# Enemy | Played | Won | Lost | Winrate\n"
		for be in data['best_enemy'][:5]:
			best_enemy_data += f"{be['nick']} | {be['played']} | {be['wins']} | {be['losses']} | {be['weighted_win_pct']}%\n"
		best_enemy_data += "```"
		embed.add_field(name='Best Enemy (Eezy Peezy)', value=best_enemy_data, inline=False)

	worst_enemy = data.get('worst_enemy')
	if worst_enemy:
		worst_enemy_data = "```markdown\n# Enemy | Played | Won | Lost | Winrate\n"
		for we in data['worst_enemy'][:5]:
			worst_enemy_data += f"{we['nick']} | {we['played']} | {we['wins']} | {we['losses']} | {we['weighted_win_pct']}%\n"
		worst_enemy_data += "```"
		embed.add_field(name='Worst Enemy (Kryptonite)', value=worst_enemy_data, inline=False)

	if pred := data.get('predictions'):
		embed.add_field(
			name='Predictions',
			value=f"Correct: {pred['correct']}/{pred['total']} ({pred['accuracy']}%) | Bet Score: {pred['bet_score']}",
			inline=False
		)

	if d := data.get('douche'):
		embed.add_field(
			name='Douche',
			value=f"Received: {d['received']} | Given: {d['given']}",
			inline=False
		)

	await ctx.reply_dm(embed=embed)


async def top(ctx, period=None):
	if period in ["day", ctx.qc.gt("day")]:
		time_gap = int(time()) - (60 * 60 * 24)
	elif period in ["week", ctx.qc.gt("week")]:
		time_gap = int(time()) - (60 * 60 * 24 * 7)
	elif period in ["month", ctx.qc.gt("month")]:
		time_gap = int(time()) - (60 * 60 * 24 * 30)
	elif period in ["year", ctx.qc.gt("year")]:
		time_gap = int(time()) - (60 * 60 * 24 * 365)
	else:
		time_gap = None

	data = await bot.stats.top(ctx.qc.id, time_gap=time_gap)
	embed = Embed(
		title=ctx.qc.gt("Top 10 players for __{target}__").format(target=f"#{ctx.channel.name}"),
		colour=Colour(0x50e3c2),
		description=ctx.qc.gt("**Total matches: {count}**").format(count=data['total'])
	)
	for p in data['players']:
		embed.add_field(name=p['nick'], value=str(p['count']), inline=True)
	await ctx.reply(embed=embed)


async def rank(ctx, player: Member = None):
	target = ctx.author if not player else await ctx.get_member(player)
	if not target:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))

	data = await ctx.qc.get_lb()
	# Figure out leaderboard placement
	if p := find(lambda i: i['user_id'] == target.id, data):
		place = data.index(p) + 1
	else:
		data = await db.select(
			['user_id', 'rating', 'deviation', 'channel_id', 'wins', 'losses', 'draws', 'is_hidden', 'streak'],
			"qc_players",
			where={'channel_id': ctx.qc.rating.channel_id}
		)
		p = find(lambda i: i['user_id'] == target.id, data)
		place = "?"

	if p:
		embed = Embed(title=f"__{get_nick(target)}__", colour=Colour(0x7289DA))
		embed.add_field(name="№", value=f"**{place}**", inline=True)
		embed.add_field(name=ctx.qc.gt("Matches"), value=f"**{(p['wins'] + p['losses'] + p['draws'])}**", inline=True)
		if p['rating']:
			embed.add_field(name=ctx.qc.gt("Rank"), value=f"**{ctx.qc.rating_rank(p['rating'])['rank']}**", inline=True)
			embed.add_field(name=ctx.qc.gt("Rating"), value=f"**{p['rating']}**±{p['deviation']}")
		else:
			embed.add_field(name=ctx.qc.gt("Rank"), value="**〈?〉**", inline=True)
			embed.add_field(name=ctx.qc.gt("Rating"), value="**?**")
		embed.add_field(
			name="W/L/D/S",
			value="**{wins}**/**{losses}**/**{draws}**/**{streak}**".format(**p),
			inline=True
		)
		embed.add_field(name=ctx.qc.gt("Winrate"), value="**{}%**\n\u200b".format(
			int(p['wins'] * 100 / (p['wins'] + p['losses'] or 1))
		), inline=True)
		if target.display_avatar:
			embed.set_thumbnail(url=target.display_avatar.url)

		changes = await db.select(
			('at', 'rating_change', 'match_id', 'reason'),
			'qc_rating_history', where=dict(user_id=target.id, channel_id=ctx.qc.rating.channel_id),
			order_by='id', limit=5
		)
		if len(changes):
			embed.add_field(
				name=ctx.qc.gt("Last changes:"),
				value="\n".join(("\u200b \u200b **{change}** \u200b | {ago} ago | {reason}{match_id}".format(
					ago=seconds_to_str(int(time() - c['at'])),
					reason=c['reason'],
					match_id=f"(__{c['match_id']}__)" if c['match_id'] else "",
					change=("+" if c['rating_change'] >= 0 else "") + str(c['rating_change'])
				) for c in changes))
			)
		await ctx.reply(embed=embed)

	else:
		raise bot.Exc.ValueError(ctx.qc.gt("No rating data found."))


async def bombayai(ctx, player: Member = None):
	from bot.bombay.ai import generate_player_summary

	if player:
		if (member := await ctx.get_member(player)) is not None:
			data = await bot.stats.user_stats(ctx.qc.id, member.id, ts_from=None, guild_id=ctx.qc.guild_id)
			target = get_nick(member)
		else:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	else:
		data = await bot.stats.user_stats(ctx.qc.id, ctx.author.id, ts_from=None, guild_id=ctx.qc.guild_id)
		target = get_nick(ctx.author)

	lines = [f"Player: {target}"]
	lines.append(f"Total matches: {data['total']}")

	if data.get('queues'):
		lines.append("Queues: " + ", ".join(f"{q['queue_name']} ({q['count']})" for q in data['queues']))

	if r := data.get('ratings'):
		lines.append(f"Record: {r['wins']}W/{r['losses']}L — {r['win_pct']}% winrate")
		lines.append(f"Rating: {r['rating']} (max {r['max_rating']}, min {r['min_rating']})")
		lines.append(f"Streak: current {r['current_streak']:+d}, best win {r['max_win_streak']}, worst loss {r['max_loss_streak']}")

	if data.get('maps'):
		lines.append("Top maps: " + ", ".join(
			f"{m['map_name']} {m['win_pct']}% wr ({m['played']} played)" for m in data['maps'][:3]
		))

	if data.get('best_ally'):
		ba = data['best_ally'][0]
		lines.append(f"Best ally: {ba['nick']} ({ba['weighted_win_pct']}% wr, {ba['played']} games)")

	if data.get('worst_ally'):
		wa = data['worst_ally'][0]
		lines.append(f"Worst ally: {wa['nick']} ({wa['weighted_win_pct']}% wr, {wa['played']} games)")

	if data.get('best_enemy'):
		be = data['best_enemy'][0]
		lines.append(f"Easiest opponent: {be['nick']} ({be['weighted_win_pct']}% wr, {be['played']} games)")

	if data.get('worst_enemy'):
		we = data['worst_enemy'][0]
		lines.append(f"Toughest opponent: {we['nick']} ({we['weighted_win_pct']}% wr, {we['played']} games)")

	if pred := data.get('predictions'):
		lines.append(f"Predictions: {pred['correct']}/{pred['total']} correct ({pred['accuracy']}% accuracy), bet score {pred['bet_score']}")

	if d := data.get('douche'):
		lines.append(f"Douche points: received {d['received']}, given {d['given']}")

	summary = await generate_player_summary("\n".join(lines))
	await ctx.reply(f"**BombayAI analysis for {target}**\n{summary}")


async def leaderboard(ctx, page: int = 1):
	page = (page or 1) - 1

	data = await ctx.qc.get_lb()
	pages = ceil(len(await ctx.qc.get_lb())/10)
	data = data[page * 10:(page + 1) * 10]
	if not len(data):
		raise bot.Exc.NotFoundError(ctx.qc.gt("Leaderboard is empty."))

	if ctx.qc.cfg.emoji_ranks:  # display as embed message
		embed = Embed(title=f"Leaderboard - page {page+1} of {pages}", colour=Colour(0x7289DA))
		embed.add_field(
			name="Nickname",
			value="\n".join((
				f'**{(page*10)+n+1}** ' + data[n]['nick'].strip()[:14]
				for n in range(len(data))
			)),
			inline=True
		)
		embed.add_field(
			name="W / L / D",
			value="\n".join((
				f"**{row['wins']}** / **{row['losses']}** / **{row['draws']}** (" +
				str(int(row['wins'] * 100 / ((row['wins'] + row['losses']) or 1))) + "%)"
				for row in data
			)),
			inline=True
		)
		embed.add_field(
			name="Rating",
			value="\n".join((
				ctx.qc.rating_rank(row['rating'])['rank'] + f" **{row['rating']}**"
				for row in data
			)),
			inline=True
		)
		await ctx.reply(embed=embed)
		return

	# display as md table
	await ctx.reply(
		discord_table(
			["№", "Rating〈Ξ〉", "Nickname", "Matches", "W/L/D"],
			[[
				(page * 10) + (n + 1),
				str(data[n]['rating']) + ctx.qc.rating_rank(data[n]['rating'])['rank'],
				data[n]['nick'].strip(),
				int(data[n]['wins'] + data[n]['losses'] + data[n]['draws']),
				"{0}/{1}/{2} ({3}%)".format(
					data[n]['wins'],
					data[n]['losses'],
					data[n]['draws'],
					int(data[n]['wins'] * 100 / ((data[n]['wins'] + data[n]['losses']) or 1))
				)
			] for n in range(len(data))]
		)
	)
