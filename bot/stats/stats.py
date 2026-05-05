# -*- coding: utf-8 -*-
import time
import datetime
import asyncio
import bot
from core.console import log
from core.database import db
from core.utils import iter_to_dict, find, get_nick

db.ensure_table(dict(
	tname="players",
	columns=[
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="name", ctype=db.types.str),
		dict(cname="allow_dm", ctype=db.types.bool),
		dict(cname="expire", ctype=db.types.int)
	],
	primary_keys=["user_id"]
))

db.ensure_table(dict(
	tname="qc_players",
	columns=[
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="is_hidden", ctype=db.types.bool, default=0),
		dict(cname="rating", ctype=db.types.int),
		dict(cname="deviation", ctype=db.types.int),
		dict(cname="wins", ctype=db.types.int, notnull=True, default=0),
		dict(cname="losses", ctype=db.types.int, notnull=True, default=0),
		dict(cname="draws", ctype=db.types.int, notnull=True, default=0),
		dict(cname="streak", ctype=db.types.int, notnull=True, default=0),
		dict(cname="last_ranked_match_at", ctype=db.types.int, notnull=False)
	],
	primary_keys=["user_id", "channel_id"]
))

db.ensure_table(dict(
	tname="qc_rating_history",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="at", ctype=db.types.int),
		dict(cname="rating_before", ctype=db.types.int),
		dict(cname="rating_change", ctype=db.types.int),
		dict(cname="deviation_before", ctype=db.types.int),
		dict(cname="deviation_change", ctype=db.types.int),
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="reason", ctype=db.types.str)
	],
	primary_keys=["id"]
))

db.ensure_table(dict(
	tname="qc_matches",
	columns=[
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="queue_id", ctype=db.types.int),
		dict(cname="queue_name", ctype=db.types.str),
		dict(cname="at", ctype=db.types.int),
		dict(cname="alpha_name", ctype=db.types.str),
		dict(cname="beta_name", ctype=db.types.str),
		dict(cname="ranked", ctype=db.types.bool),
		dict(cname="winner", ctype=db.types.bool),
		dict(cname="alpha_score", ctype=db.types.int),
		dict(cname="beta_score", ctype=db.types.int),
		dict(cname="maps", ctype=db.types.str)
	],
	primary_keys=["match_id"]
))

db.ensure_table(dict(
	tname="qc_match_id_counter",
	columns=[
		dict(cname="next_id", ctype=db.types.int)
	]
))

db.ensure_table(dict(
	tname="qc_player_matches",
	columns=[
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="team", ctype=db.types.bool)
	],
	primary_keys=["match_id", "user_id"]
))

db.ensure_table(dict(
	tname="disabled_guilds",
	columns=[
		dict(cname="guild_id", ctype=db.types.int)
	],
	primary_keys=["guild_id"]
))


async def check_match_id_counter():
	"""
	Set to current max match_id+1 if not persist or less
	"""
	m = await db.select_one(('match_id',), 'qc_matches', order_by='match_id', limit=1)
	next_known_match = m['match_id']+1 if m else 0
	counter = await db.select_one(('next_id',), 'qc_match_id_counter')
	if counter is None:
		await db.insert('qc_match_id_counter', dict(next_id=next_known_match))
	elif next_known_match > counter['next_id']:
		await db.update('qc_match_id_counter', dict(next_id=next_known_match))


async def next_match():
	""" Increase match_id counter, return current match_id """
	counter = await db.select_one(('next_id',), 'qc_match_id_counter')
	await db.update('qc_match_id_counter', dict(next_id=counter['next_id']+1))
	log.debug(f"Current match_id is {counter['next_id']}")
	return counter['next_id']


async def register_match_unranked(ctx, m):
	await db.insert('qc_matches', dict(
		match_id=m.id, channel_id=m.qc.id, queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
		alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
		at=int(time.time()), ranked=0, winner=None, maps="\n".join(m.maps)
	))

	await db.insert_many('qc_players', (
		dict(channel_id=m.qc.id, user_id=p.id)
		for p in m.players
	), on_dublicate="ignore")

	for p in m.players:
		nick = get_nick(p)
		await db.update(
			"qc_players",
			dict(nick=nick),
			keys=dict(channel_id=m.qc.id, user_id=p.id)
		)

		if p in m.teams[0]:
			team = 0
		elif p in m.teams[1]:
			team = 1
		else:
			team = None

		await db.insert(
			'qc_player_matches',
			dict(match_id=m.id, channel_id=m.qc.id, user_id=p.id, nick=nick, team=team)
		)


async def register_match_ranked(ctx, m):
	now = int(time.time())

	await db.insert('qc_matches', dict(
		match_id=m.id, channel_id=m.qc.id, queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
		alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
		at=now, ranked=1, winner=m.winner,
		alpha_score=m.scores[0], beta_score=m.scores[1], maps="\n".join(m.maps)
	))

	for channel_id in {m.qc.id, m.qc.rating.channel_id}:
		await db.insert_many('qc_players', (
			dict(channel_id=channel_id, user_id=p.id, nick=get_nick(p))
			for p in m.players
		), on_dublicate="ignore")

	results = [[
		await m.qc.rating.get_players((p.id for p in m.teams[0])),
		await m.qc.rating.get_players((p.id for p in m.teams[1])),
	]]

	if m.winner is None:  # draw
		after = m.qc.rating.rate(winners=results[0][0], losers=results[0][1], draw=True)
		results.append(after)
	else:  # process actual scores
		n = 0
		while n < m.scores[0] or n < m.scores[1]:
			if n < m.scores[0]:
				after = m.qc.rating.rate(winners=results[-1][0], losers=results[-1][1], draw=False)
				results.append(after)
			if n < m.scores[1]:
				after = m.qc.rating.rate(winners=results[-1][1], losers=results[-1][0], draw=False)
				results.append(after[::-1])
			n += 1

	after = iter_to_dict((*results[-1][0], *results[-1][1]), key='user_id')
	before = iter_to_dict((*results[0][0], *results[0][1]), key='user_id')

	for p in m.players:
		nick = get_nick(p)
		team = 0 if p in m.teams[0] else 1

		await db.update(
			"qc_players",
			dict(
				nick=nick,
				rating=after[p.id]['rating'],
				deviation=after[p.id]['deviation'],
				wins=after[p.id]['wins'],
				losses=after[p.id]['losses'],
				draws=after[p.id]['draws'],
				streak=after[p.id]['streak'],
				last_ranked_match_at=now,
			),
			keys=dict(channel_id=m.qc.rating.channel_id, user_id=p.id)
		)

		await db.insert(
			'qc_player_matches',
			dict(match_id=m.id, channel_id=m.qc.id, user_id=p.id, nick=nick, team=team)
		)
		await db.insert('qc_rating_history', dict(
			channel_id=m.qc.rating.channel_id,
			user_id=p.id,
			at=now,
			rating_before=before[p.id]['rating'],
			rating_change=after[p.id]['rating']-before[p.id]['rating'],
			deviation_before=before[p.id]['deviation'],
			deviation_change=after[p.id]['deviation']-before[p.id]['deviation'],
			match_id=m.id,
			reason=m.queue.name
		))

	await m.qc.update_rating_roles(*m.players)
	await m.print_rating_results(ctx, before, after)


async def undo_match(ctx, match_id):
	match = await db.select_one(('ranked', 'winner'), 'qc_matches', where=dict(match_id=match_id, channel_id=ctx.qc.id))
	if not match:
		return False

	if match['ranked']:
		p_matches = await db.select(('user_id', 'team'), 'qc_player_matches', where=dict(match_id=match_id))
		p_history = iter_to_dict(
			await db.select(
				('user_id', 'rating_change', 'deviation_change'), 'qc_rating_history', where=dict(match_id=match_id)
			), key='user_id'
		)
		stats = iter_to_dict(
			await ctx.qc.rating.get_players((p['user_id'] for p in p_matches)), key='user_id'
		)

		for p in p_matches:
			new = stats[p['user_id']]
			changes = p_history[p['user_id']]

			print(match['winner'])
			if match['winner'] is None:
				new['draws'] = max((new['draws'] - 1, 0))
			elif match['winner'] == p['team']:
				new['wins'] = max((new['wins'] - 1, 0))
			else:
				new['losses'] = max((new['losses'] - 1, 0))

			new['rating'] = max((new['rating']-changes['rating_change'], 0))
			new['deviation'] = max((new['deviation']-changes['deviation_change'], 0))

			await db.update("qc_players", new, keys=dict(channel_id=ctx.qc.rating.channel_id, user_id=p['user_id']))
		await db.delete("qc_rating_history", where=dict(match_id=match_id))
		members = (ctx.channel.guild.get_member(p['user_id']) for p in p_matches)
		await ctx.qc.update_rating_roles(*(m for m in members if m is not None))

	await db.delete('qc_player_matches', where=dict(match_id=match_id))
	await db.delete('qc_matches', where=dict(match_id=match_id))
	return True


async def reset_channel(channel_id):
	where = {'channel_id': channel_id}
	await db.delete("qc_players", where=where)
	await db.delete("qc_rating_history", where=where)
	await db.delete("qc_matches", where=where)
	await db.delete("qc_player_matches", where=where)


async def reset_player(channel_id, user_id):
	where = {'channel_id': channel_id, 'user_id': user_id}
	await db.delete("qc_players", where=where)
	await db.delete("qc_rating_history", where=where)
	await db.delete("qc_player_matches", where=where)


async def replace_player(channel_id, user_id1, user_id2, new_nick):
	await db.delete("qc_players", {'channel_id': channel_id, 'user_id': user_id2})
	where = {'channel_id': channel_id, 'user_id': user_id1}
	await db.update("qc_players", {'user_id': user_id2, 'nick': new_nick}, where)
	await db.update("qc_rating_history", {'user_id': user_id2}, where)
	await db.update("qc_player_matches", {'user_id': user_id2}, where)


async def qc_stats(channel_id, ts_from=None):
	at_filter = " AND `at` >= %s" if ts_from else ""
	params = [channel_id] + ([ts_from] if ts_from else [])

	data = await db.fetchall(
		"SELECT `queue_name`, COUNT(*) as count FROM `qc_matches` WHERE `channel_id`=%s" +
		at_filter + " GROUP BY `queue_name` ORDER BY count DESC",
		params
	)
	stats = dict(total=sum((i['count'] for i in data)))
	stats['queues'] = data
	return stats


async def user_stats(channel_id, user_id, ts_from=None, guild_id=None):
	# Build date filter fragments once, reused across all queries
	def _df(col):
		return f" AND {col} >= %s" if ts_from else ""

	dp = [ts_from] if ts_from else []  # date params list

	queue_data = await db.fetchall(
		"SELECT `queue_name`, COUNT(*) as count FROM `qc_player_matches` AS pm " +
		"JOIN `qc_matches` AS m ON pm.match_id=m.match_id " +
		"WHERE pm.channel_id=%s AND user_id=%s" +
		_df("m.at") + " GROUP BY m.queue_name ORDER BY count DESC",
		[channel_id, user_id, *dp]
	)

	hat_df = _df("h.at")
	ratings_data = await db.fetchone(
		f"""
		WITH match_results AS (
			SELECT at,
				CASE WHEN rating_change > 0 THEN 1 WHEN rating_change < 0 THEN -1 ELSE 0 END AS result
			FROM qc_rating_history h
			WHERE channel_id = %s AND user_id = %s AND match_id IS NOT NULL{hat_df}
		),
		grouped AS (
			SELECT at, result,
				ROW_NUMBER() OVER (ORDER BY at) -
				ROW_NUMBER() OVER (PARTITION BY result ORDER BY at) AS grp
			FROM match_results
		),
		streaks AS (
			SELECT result, COUNT(*) AS streak_len
			FROM grouped
			GROUP BY result, grp
		)
		SELECT
			h.user_id,
			p.rating,
			MAX(h.rating_before + h.rating_change) AS max_rating,
			MIN(h.rating_before + h.rating_change) AS min_rating,
			MAX(CASE WHEN h.rating_before + h.rating_change = (
				SELECT MAX(rating_before + rating_change)
				FROM qc_rating_history h
				WHERE channel_id = %s AND user_id = %s{hat_df}
			) THEN from_unixtime(h.at) END) AS max_rating_at,
			MAX(CASE WHEN h.rating_before + h.rating_change = (
				SELECT MIN(rating_before + rating_change)
				FROM qc_rating_history h
				WHERE channel_id = %s AND user_id = %s{hat_df}
			) THEN from_unixtime(h.at) END) AS min_rating_at,
			p.wins,
			p.losses,
			ROUND(p.wins / NULLIF(p.wins + p.losses, 0) * 100, 1) AS win_pct,
			p.streak AS current_streak,
			COALESCE((SELECT MAX(streak_len) FROM streaks WHERE result = 1), 0) AS max_win_streak,
			COALESCE((SELECT MAX(streak_len) FROM streaks WHERE result = -1), 0) AS max_loss_streak
		FROM qc_rating_history h
		JOIN qc_players p ON p.channel_id = h.channel_id AND p.user_id = h.user_id
		WHERE h.channel_id = %s AND h.user_id = %s{hat_df}
		GROUP BY h.user_id, p.wins, p.losses, p.draws, p.streak
		""",
		[
			channel_id, user_id, *dp,  # match_results CTE
			channel_id, user_id, *dp,  # max_rating_at subquery
			channel_id, user_id, *dp,  # min_rating_at subquery
			channel_id, user_id, *dp,  # outer WHERE
		]
	)

	at_df = _df("at")
	map_data = await db.fetchall(
		f"""
		WITH RECURSIVE map_split AS (
			SELECT
				match_id,
				channel_id,
				winner,
				TRIM(SUBSTRING_INDEX(maps, '\n', 1)) AS map_name,
				IF(LOCATE('\n', maps) > 0, SUBSTRING(maps, LOCATE('\n', maps) + 1), NULL) AS remaining
			FROM qc_matches
			WHERE channel_id = %s AND maps IS NOT NULL AND maps != ''{at_df}

			UNION ALL

			SELECT
				match_id,
				channel_id,
				winner,
				TRIM(SUBSTRING_INDEX(remaining, '\n', 1)),
				IF(LOCATE('\n', remaining) > 0, SUBSTRING(remaining, LOCATE('\n', remaining) + 1), NULL)
			FROM map_split
			WHERE remaining IS NOT NULL
		)
		SELECT
			ms.map_name,
			COUNT(*) AS played,
			SUM(CASE WHEN pm.team = ms.winner THEN 1 ELSE 0 END) AS wins,
			SUM(CASE WHEN ms.winner IS NOT NULL AND pm.team != ms.winner THEN 1 ELSE 0 END) AS losses,
			ROUND(
				SUM(CASE WHEN pm.team = ms.winner THEN 1 ELSE 0 END) /
				NULLIF(SUM(CASE WHEN ms.winner IS NOT NULL THEN 1 ELSE 0 END), 0) * 100, 1
			) AS win_pct
		FROM map_split ms
		JOIN qc_player_matches pm ON ms.match_id = pm.match_id AND ms.channel_id = pm.channel_id
		WHERE pm.user_id = %s
		GROUP BY ms.map_name
		HAVING played > 10
		ORDER BY win_pct DESC
		""",
		[channel_id, *dp, user_id]
	)

	mat_df = _df("m.at")
	_ally_cte = f"""
		WITH ally_stats AS (
			SELECT
				p.nick,
				COUNT(*) AS played,
				SUM(CASE WHEN m.winner = pm1.team THEN 1 ELSE 0 END) AS wins,
				SUM(CASE WHEN m.winner IS NOT NULL AND m.winner != pm1.team THEN 1 ELSE 0 END) AS losses,
				ROUND(
					SUM(CASE WHEN m.winner = pm1.team THEN 1 ELSE 0 END) /
					NULLIF(SUM(CASE WHEN m.winner IS NOT NULL THEN 1 ELSE 0 END), 0) * 100, 1
				) AS win_pct,
				ROUND(
					(SUM(CASE WHEN m.winner = pm1.team THEN 1 ELSE 0 END) + 7.5) /
					(COUNT(*) + 15) * 100, 1
				) AS weighted_win_pct
			FROM qc_player_matches pm1
			JOIN qc_player_matches pm2
				ON pm1.match_id = pm2.match_id
				AND pm1.channel_id = pm2.channel_id
				AND pm1.team = pm2.team
				AND pm2.user_id != pm1.user_id
			JOIN qc_matches m ON pm1.match_id = m.match_id AND pm1.channel_id = m.channel_id
			JOIN qc_players p ON p.user_id = pm2.user_id AND p.channel_id = pm2.channel_id
			WHERE pm1.channel_id = %s AND pm1.user_id = %s{mat_df}
			GROUP BY p.nick
			HAVING played >= 15
		)
	"""
	_enemy_cte = f"""
		WITH enemy_stats AS (
			SELECT
				p.nick,
				COUNT(*) AS played,
				SUM(CASE WHEN m.winner = pm1.team THEN 1 ELSE 0 END) AS wins,
				SUM(CASE WHEN m.winner IS NOT NULL AND m.winner != pm1.team THEN 1 ELSE 0 END) AS losses,
				ROUND(
					SUM(CASE WHEN m.winner = pm1.team THEN 1 ELSE 0 END) /
					NULLIF(SUM(CASE WHEN m.winner IS NOT NULL THEN 1 ELSE 0 END), 0) * 100, 1
				) AS win_pct,
				ROUND(
					(SUM(CASE WHEN m.winner = pm1.team THEN 1 ELSE 0 END) + 7.5) /
					(COUNT(*) + 15) * 100, 1
				) AS weighted_win_pct
			FROM qc_player_matches pm1
			JOIN qc_player_matches pm2
				ON pm1.match_id = pm2.match_id
				AND pm1.channel_id = pm2.channel_id
				AND pm1.team != pm2.team
			JOIN qc_matches m ON pm1.match_id = m.match_id AND pm1.channel_id = m.channel_id
			JOIN qc_players p ON p.user_id = pm2.user_id AND p.channel_id = pm2.channel_id
			WHERE pm1.channel_id = %s AND pm1.user_id = %s{mat_df}
			GROUP BY p.nick
			HAVING played >= 15
		)
	"""
	cte_params = [channel_id, user_id, *dp]
	best_ally_data = await db.fetchall(
		_ally_cte + "SELECT nick, played, wins, losses, win_pct, weighted_win_pct FROM ally_stats ORDER BY weighted_win_pct DESC LIMIT 5",
		cte_params
	)
	worst_ally_data = await db.fetchall(
		_ally_cte + "SELECT nick, played, wins, losses, win_pct, weighted_win_pct FROM ally_stats ORDER BY weighted_win_pct ASC LIMIT 5",
		cte_params
	)
	best_enemy_data = await db.fetchall(
		_enemy_cte + "SELECT nick, played, wins, losses, win_pct, weighted_win_pct FROM enemy_stats ORDER BY weighted_win_pct DESC LIMIT 5",
		cte_params
	)
	worst_enemy_data = await db.fetchall(
		_enemy_cte + "SELECT nick, played, wins, losses, win_pct, weighted_win_pct FROM enemy_stats ORDER BY weighted_win_pct ASC LIMIT 5",
		cte_params
	)
	predictions_data = None
	douche_data = None
	if guild_id:
		predictions_data, douche_data = await asyncio.gather(
			db.fetchone(
				"""
				SELECT COUNT(*) AS total,
					SUM(CASE WHEN m.winner = p.team THEN 1 ELSE 0 END) AS correct,
					ROUND(SUM(CASE WHEN m.winner = p.team THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS accuracy,
					ROUND(SUM(CASE WHEN m.winner = p.team THEN 100.0 / NULLIF(p.win_prob, 0) ELSE 0 END), 1) AS bet_score
				FROM predictions p
				JOIN qc_matches m ON p.match_id = m.match_id
				WHERE p.guild_id = %s AND p.user_id = %s AND m.winner IS NOT NULL
				""",
				[guild_id, user_id]
			),
			db.fetchone(
				"""
				SELECT
					(SELECT COUNT(*) FROM douche WHERE guild_id = %s AND target_user_id = %s) AS received,
					(SELECT COUNT(*) FROM douche WHERE guild_id = %s AND user_id = %s) AS given
				""",
				[guild_id, user_id, guild_id, user_id]
			)
		)

	stats = dict(total=sum((i['count'] for i in queue_data)))
	stats['queues'] = queue_data
	stats['ratings'] = ratings_data
	stats['maps'] = map_data
	stats['best_ally'] = best_ally_data
	stats['best_enemy'] = best_enemy_data
	stats['worst_ally'] = worst_ally_data
	stats['worst_enemy'] = worst_enemy_data
	stats['predictions'] = predictions_data if predictions_data and predictions_data['total'] else None
	stats['douche'] = douche_data if douche_data and (douche_data['received'] or douche_data['given']) else None
	return stats


async def top(channel_id, time_gap=None):
	total = await db.fetchone(
		"SELECT COUNT(*) as count FROM `qc_matches` WHERE channel_id=%s" + (f" AND at>{time_gap} " if time_gap else ""),
		(channel_id, )
	)

	data = await db.fetchall(
		"SELECT p.nick as nick, COUNT(*) as count FROM `qc_player_matches` AS pm " +
		"JOIN `qc_players` AS p ON pm.user_id=p.user_id AND pm.channel_id=p.channel_id " +
		"JOIN `qc_matches` AS m ON pm.match_id=m.match_id " +
		"WHERE pm.channel_id=%s " +
		(f"AND m.at>{time_gap} " if time_gap else "") +
		"GROUP BY p.user_id ORDER BY count DESC LIMIT 10",
		(channel_id, )
	)
	stats = dict(total=total['count'])
	stats['players'] = data
	return stats


async def last_games(channel_id):
	#  get last played ranked match for all players
	data = await db.fetchall(
		"SELECT tmp.at, p.* " +
		"FROM `qc_players` AS p " +
		"LEFT JOIN (" +
		"  SELECT MAX(h.at) AS at, h.user_id FROM `qc_rating_history` AS h" +
		"    WHERE h.channel_id=%s AND h.match_id IS NOT NULL" +
		"    GROUP BY h.user_id" +
		") AS tmp ON p.user_id=tmp.user_id " +
		"WHERE p.channel_id=%s",
		(channel_id, channel_id)
	)
	return data


class StatsJobs:

	def __init__(self):
		self.next_decay_at = int(self.next_monday().timestamp())

	@staticmethod
	def next_monday():
		d = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
		d += datetime.timedelta(days=1)
		while d.weekday() != 0:  # 0 for monday
			d += datetime.timedelta(days=1)
		return d

	@staticmethod
	def tomorrow():
		d = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
		d += datetime.timedelta(days=1)
		return d

	@staticmethod
	async def apply_rating_decays():
		log.info("--- Applying weekly deviation decays ---")
		for qc in bot.queue_channels.values():
			await qc.apply_rating_decay()
			await asyncio.sleep(1)

	async def think(self, frame_time):
		if frame_time > self.next_decay_at:
			self.next_decay_at = int(self.next_monday().timestamp())
			asyncio.create_task(self.apply_rating_decays())


jobs = StatsJobs()
