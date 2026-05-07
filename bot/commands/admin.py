__all__ = [
	'noadds', 'noadd', 'forgive', 'rating_seed', 'rating_penality', 'rating_hide',
	'rating_reset', 'rating_snap', 'stats_reset', 'stats_reset_player', 'stats_replace_player',
	'phrases_add', 'phrases_clear', 'undo_match', 'douche_add', 'douche_leaderboard', 'douche_summary',
	'predictions_leaderboard'
]

from time import time
from datetime import timedelta
from nextcord import Member

from core.utils import seconds_to_str, get_nick, discord_table

import bot


async def noadds(ctx):
	data = await bot.noadds.get_noadds(ctx)
	now = int(time())
	s = "```markdown\n"
	s += ctx.qc.gt(" ID | Prisoner | Left | Reason")
	s += "\n----------------------------------------\n"
	if len(data):
		s += "\n".join((
			f" {i['id']} | {i['name']} | {seconds_to_str(max(0, (i['at'] + i['duration']) - now))} | {i['reason'] or '-'}"
			for i in data
		))
	else:
		s += ctx.qc.gt("Noadds are empty.")
	await ctx.reply(s + "\n```")


async def noadd(ctx, player: Member, duration: timedelta, reason: str = None):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if not duration:
		duration = timedelta(hours=2)
	if duration > timedelta(days=365*100):
		raise bot.Exc.ValueError(ctx.qc.gt("Specified duration time is too long."))
	await bot.noadds.noadd(
		ctx=ctx, member=player, duration=int(duration.total_seconds()), moderator=ctx.author, reason=reason
	)
	await ctx.success(ctx.qc.gt("Banned **{member}** for `{duration}`.").format(
		member=get_nick(player),
		duration=duration.__str__()
	))


async def forgive(ctx, player: Member):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if await bot.noadds.forgive(ctx=ctx, member=player, moderator=ctx.author):
		await ctx.success(ctx.qc.gt("Done."))
	else:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified member is not banned."))


async def rating_seed(ctx, player: str, rating: int, deviation: int = None):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")
	if not 0 < rating < 10000 or not 0 < (deviation or 1) < 3000:
		raise bot.Exc.ValueError("Bad rating or deviation value.")

	await ctx.qc.rating.set_rating(player, rating=rating, deviation=deviation, reason="manual seeding")
	await ctx.qc.update_rating_roles(player)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_penality(ctx, player: str, penality: int, reason: str = None):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")
	if abs(penality) > 10000:
		raise ValueError("Bad penality value.")
	reason = "penality: " + reason if reason else "penality by a moderator"

	await ctx.qc.rating.set_rating(player, penality=penality, reason=reason)
	await ctx.qc.update_rating_roles(player)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_hide(ctx, player: str, hide: bool = True):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")
	await ctx.qc.rating.hide_player(player.id, hide=hide)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_reset(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	await ctx.qc.rating.reset()
	await ctx.success(ctx.qc.gt("Done."))


async def rating_snap(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	await ctx.qc.rating.snap_ratings(ctx.qc._ranks_table)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_reset(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	await bot.stats.reset_channel(ctx.qc.id)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_reset_player(ctx, player: str):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")

	await bot.stats.reset_player(ctx.qc.id, player.id)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_replace_player(ctx, player1: str, player2: str):
	ctx.check_perms(ctx.Perms.ADMIN)
	if (player1 := await ctx.get_member(player1)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")
	if (player2 := await ctx.get_member(player2)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")

	await bot.stats.replace_player(ctx.qc.id, player1.id, player2.id, get_nick(player2))
	await ctx.success(ctx.qc.gt("Done."))


async def phrases_add(ctx, player: Member, phrase: str):
	ctx.check_perms(ctx.Perms.MODERATOR)
	await bot.noadds.phrases_add(ctx, player, phrase)
	await ctx.success(ctx.qc.gt("Done."))


async def phrases_clear(ctx, player: Member):
	ctx.check_perms(ctx.Perms.MODERATOR)
	await bot.noadds.phrases_clear(ctx, member=player)
	await ctx.success(ctx.qc.gt("Done."))

async def douche_add(ctx, player: Member, target: Member = None):
	ctx.check_perms(ctx.Perms.MODERATOR)
	await bot.douche.douche_add(ctx, member=player, moderator=ctx.author, target= target)
	await ctx.success(ctx.qc.gt("Done."))

async def douche_summary(ctx, player: Member):
	summary = await bot.douche.get_user_summary(ctx, player)
	s = f"```markdown\n# Douche summary for {get_nick(player)}\n"
	s += f"Received: {summary['received']} | Given: {summary['given']}\n"
	if summary['recent']:
		s += "\nRecent douche targets:\n"
		s += "\n".join((
			f"  -> {r['target_name']}"
			for r in summary['recent']
		))
	await ctx.reply(s + "\n```")


async def douche_leaderboard(ctx):
	data = await bot.douche.get_leaderboard(ctx)
	if not data:
		await ctx.reply("```No douche data yet.```")
		return
	await ctx.reply(discord_table(
		["#", "Player", "Count"],
		[[i + 1, row['name'][:16], row['count']] for i, row in enumerate(data)]
	))

async def predictions_leaderboard(ctx, page: int = 1, season: int = None):
	from core.database import db
	from asyncio import gather
	from itertools import groupby
	from datetime import date, datetime, time as dtime

	PAGE_SIZE = 10
	page = (page or 1) - 1

	guild_id = ctx.channel.guild.id

	start = date(2026, 4, 1)

	def add_months(d: date, months: int) -> date:
		idx = d.year * 12 + (d.month - 1) + months
		year, month = divmod(idx, 12)
		return date(year, month + 1, 1)

	today = date.today()
	current_season = (today.year * 12 + today.month) - (start.year * 12 + start.month) + 1
	query_season = current_season if season is None else max(1, min(season, current_season))
	month_start = add_months(start, query_season - 1)
	month_end = add_months(month_start, 1)
	from_ts = int(datetime.combine(month_start, dtime.min).timestamp())
	to_ts = int(datetime.combine(month_end, dtime.min).timestamp())

	data, streak_rows = await gather(
		db.fetchall(
			"""
			SELECT p.user_id, COALESCE(qp.nick, CAST(p.user_id AS CHAR)) AS name,
				COUNT(*) AS total,
				SUM(CASE WHEN m.winner = p.team THEN 1 ELSE 0 END) AS correct,
				ROUND(SUM(CASE WHEN m.winner = p.team THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS accuracy,
				SUM(CASE WHEN p.at >= %s AND p.at < %s THEN 1 ELSE 0 END) AS season_total,
				SUM(CASE WHEN p.at >= %s AND p.at < %s AND m.winner = p.team THEN 1 ELSE 0 END) AS season_correct,
				ROUND(SUM(CASE WHEN p.at >= %s AND p.at < %s AND m.winner = p.team THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN p.at >= %s AND p.at < %s THEN 1 ELSE 0 END), 0) * 100, 1) AS season_accuracy,
				1000 + ROUND(SUM(CASE WHEN p.at >= %s AND p.at < %s THEN COALESCE(wa.avg_change, 0) ELSE 0 END), 0) AS rating_pts
			FROM predictions p
			JOIN qc_matches m ON p.match_id = m.match_id
			LEFT JOIN qc_players qp ON qp.user_id = p.user_id AND qp.channel_id = m.channel_id
			LEFT JOIN (
				SELECT pm.match_id, pm.team, AVG(rh.rating_change) AS avg_change
				FROM qc_player_matches pm
				JOIN qc_rating_history rh ON rh.match_id = pm.match_id AND rh.user_id = pm.user_id
				GROUP BY pm.match_id, pm.team
			) wa ON wa.match_id = p.match_id AND wa.team = p.team
			WHERE p.guild_id = %s AND m.winner IS NOT NULL
			GROUP BY p.user_id, name
			HAVING season_total > 0
			ORDER BY rating_pts DESC
			""",
			[from_ts, to_ts, from_ts, to_ts, from_ts, to_ts, from_ts, to_ts, from_ts, to_ts, guild_id]
		),
		db.fetchall(
			"""
			SELECT p.user_id, CASE WHEN m.winner = p.team THEN 1 ELSE 0 END AS correct
			FROM predictions p
			JOIN qc_matches m ON p.match_id = m.match_id
			WHERE p.guild_id = %s AND m.winner IS NOT NULL AND p.at >= %s AND p.at < %s
			ORDER BY p.user_id, p.at DESC
			""",
			[guild_id, from_ts, to_ts]
		)
	)
	if not data:
		await ctx.reply("```No prediction data yet.```")
		return

	streaks = {}
	for user_id, preds in groupby(streak_rows, key=lambda r: r['user_id']):
		first = next(preds, None)
		if first is None:
			streaks[user_id] = 0
			continue
		direction = 1 if first['correct'] else -1
		streak = direction
		for p in preds:
			if (p['correct'] and direction == 1) or (not p['correct'] and direction == -1):
				streak += direction
			else:
				break
		streaks[user_id] = streak

	total_pages = max(1, (len(data) + PAGE_SIZE - 1) // PAGE_SIZE)
	page = max(0, min(page, total_pages - 1))
	offset = page * PAGE_SIZE
	rows = data[offset:offset + PAGE_SIZE]

	def _streak_str(s):
		return f"+{s}" if s > 0 else str(s)

	def _pct(v):
		return f"{float(v):g}%" if v is not None else "—"

	table = discord_table(
		["#", "Player", "Overall", "Season", "Streak", "Score"],
		[
			[
				offset + i + 1,
				row['name'][:16],
				f"{_pct(row['accuracy'])} ({row['correct']}/{row['total']})",
				f"{_pct(row['season_accuracy'])} ({row['season_correct']}/{row['season_total']})",
				_streak_str(streaks.get(row['user_id'], 0)),
				int(row['rating_pts'] or 0),
			]
			for i, row in enumerate(rows)
		]
	)
	heading = f"# Season {query_season} — {month_start.strftime('%B %Y')}"
	if total_pages > 1:
		heading += f"  (page {page + 1}/{total_pages})"
	table = table.replace("```markdown\n", f"```markdown\n{heading}\n", 1)
	await ctx.reply(table)


async def undo_match(ctx, match_id: int):
	ctx.check_perms(ctx.Perms.MODERATOR)

	result = await bot.stats.undo_match(ctx, match_id)
	if result:
		await ctx.success(ctx.qc.gt("Done."))
	else:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Could not find match with specified id."))
