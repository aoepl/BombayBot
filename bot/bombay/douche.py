# -*- coding: utf-8 -*-
import time
from random import choice
from core.database import db
from core.utils import get_nick

db.ensure_table(dict(
	tname="douche",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="guild_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="name", ctype=db.types.str),
        dict(cname="target_user_id", ctype=db.types.int),
        dict(cname="target_name", ctype=db.types.str),
		dict(cname="at", ctype=db.types.int),
        dict(cname="by", ctype=db.types.str)
	],
	primary_keys=["id"]
))

class Douche:

	def __init__(self):
		self.next_tick = 0

	@staticmethod
	async def douche_add(ctx, member, target, moderator):
		await db.insert('douche', dict(
			guild_id=ctx.channel.guild.id,
			user_id=member.id,
			target_id=target.id,
			name=get_nick(member),
			target_name=get_nick(target),
			at=int(time.time()),
			by=get_nick(moderator)
		))

	@staticmethod
	async def get_user_summary(ctx, member):
		received = await db.fetchone(
			"SELECT COUNT(*) AS count FROM douche WHERE guild_id=%s AND target_user_id=%s",
			[ctx.channel.guild.id, member.id]
		)
		given = await db.fetchone(
			"SELECT COUNT(*) AS count FROM douche WHERE guild_id=%s AND user_id=%s",
			[ctx.channel.guild.id, member.id]
		)
		recent = await db.fetchall(
			"SELECT target_name, at FROM douche WHERE guild_id=%s AND user_id=%s ORDER BY at DESC LIMIT 5",
			[ctx.channel.guild.id, member.id]
		)
		return dict(
			received=received['count'] if received else 0,
			given=given['count'] if given else 0,
			recent=recent or []
		)

	@staticmethod
	async def get_leaderboard(ctx, limit=10):
		return await db.fetchall(
			"SELECT user_id, name, COUNT(*) AS count FROM douche WHERE guild_id=%s GROUP BY user_id ORDER BY count DESC LIMIT %s",
			[ctx.channel.guild.id, limit]
		)

douche = Douche()
