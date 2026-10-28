# -*- coding: utf-8 -*-
"""
MCP server for /ask: read-only access to the bot's MySQL or MariaDB database (tables, columns and SELECT queries).

Every query runs in a READ ONLY transaction with a time limit, and only SELECT, WITH, SHOW, DESCRIBE and EXPLAIN
are accepted. Set ASK_DB_URI to a MySQL user that can only SELECT to add a second guard; without it the bot's own
connection (DB_URI) is used.
"""
import json
import re

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from core.config import cfg
from core.database import db, init_db

MAX_ROWS = 200           # rows a query can return at most
DEFAULT_ROWS = 50
CELL_CHARS = 300         # one value is cut to this
TIME_LIMIT_MS = 5000     # per query

_READ = re.compile(r'^\s*(select|with|show|describe|desc|explain)\b', re.I)
_FORBIDDEN = re.compile(
	r'\binto\s+(outfile|dumpfile|@)|\bfor\s+(update|share)\b|\block\s+in\s+share\b|\b(load_file|sleep|benchmark|get_lock)\s*\('
	r'|\b(mysql|sys|performance_schema)\s*\.', re.I
)

_ask_db = None
_version = None  # the database server's VERSION(), e.g. '8.4.3' or '10.11.6-MariaDB'


async def _db():
	""" The read-only connection when ASK_DB_URI is set, else the bot's own. """
	global _ask_db
	if not cfg.ASK_DB_URI:
		return db
	if _ask_db is None:
		_ask_db = init_db(cfg.ASK_DB_URI)
		await _ask_db.connect()
	return _ask_db


async def _server_version(cur) -> str:
	global _version
	if _version is None:
		await cur.execute('SELECT VERSION() AS v')
		_version = (await cur.fetchone())['v']
	return _version


async def _set_time_limit(cur, on: bool):
	""" MySQL limits a query with MAX_EXECUTION_TIME (ms, SELECT only), MariaDB with max_statement_time (s). """
	if 'mariadb' in (await _server_version(cur)).lower():
		await cur.execute('SET SESSION max_statement_time = %s', (TIME_LIMIT_MS / 1000 if on else 0,))
	else:
		await cur.execute('SET SESSION MAX_EXECUTION_TIME = %s', (TIME_LIMIT_MS if on else 0,))


def _check(sql: str) -> str:
	sql = sql.strip().rstrip(';').strip()
	if not _READ.match(sql):
		raise ToolError("Only SELECT, WITH, SHOW, DESCRIBE and EXPLAIN queries are allowed.")
	if ';' in sql:
		raise ToolError("One statement per query.")
	if _FORBIDDEN.search(sql):
		raise ToolError("That query uses something that isn't allowed (locking, files, sleep or system schemas).")
	return sql


def _cell(v):
	if isinstance(v, (bytes, bytearray)):
		v = v.decode('utf-8', 'replace')
	if isinstance(v, str) and len(v) > CELL_CHARS:
		return v[:CELL_CHARS] + '…'
	return v


async def _read(sql: str, args=None, max_rows: int = MAX_ROWS):
	""" (rows, more): rows from one read-only query, more is True when it had more than max_rows. """
	conn_db = await _db()
	async with conn_db.pool.acquire() as conn:
		async with conn.cursor() as cur:
			await _set_time_limit(cur, True)
			await cur.execute('START TRANSACTION READ ONLY')
			try:
				await cur.execute(sql, args)
				rows = await cur.fetchmany(max_rows + 1)
			except Exception as e:  # noqa: BLE001 - the model gets the database error and can fix its query
				raise ToolError(str(e))
			finally:
				await cur.execute('ROLLBACK')
				await _set_time_limit(cur, False)
	return [{k: _cell(v) for k, v in r.items()} for r in rows[:max_rows]], len(rows) > max_rows


def build_sql_server() -> MCPServer:
	server = MCPServer("bombaybot-sql", instructions="Read-only access to BombayBot's MySQL database.")

	@server.tool()
	async def list_tables() -> str:
		""" The database server's version, then every table with its approximate row count and its columns. """
		tables, _ = await _read(
			"SELECT TABLE_NAME AS t, TABLE_ROWS AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
			"ORDER BY TABLE_NAME", max_rows=500)
		columns, _ = await _read(
			"SELECT TABLE_NAME AS t, COLUMN_NAME AS c, COLUMN_TYPE AS type FROM information_schema.COLUMNS "
			"WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION", max_rows=5000)
		by_table = {}
		for c in columns:
			by_table.setdefault(c['t'], []).append(f"{c['c']} {c['type']}")
		return f"Server: {_version}\n" + '\n'.join(f"{t['t']} (~{t['n'] or 0} rows): {', '.join(by_table.get(t['t'], []))}" for t in tables)

	@server.tool()
	async def query(sql: str, max_rows: int = DEFAULT_ROWS) -> str:
		"""
		Run one read-only query (MySQL or MariaDB, see list_tables for which) (SELECT, WITH, SHOW, DESCRIBE or EXPLAIN) and get the rows as JSON.
		Aggregate in SQL (COUNT, AVG, GROUP BY, ORDER BY ... LIMIT) instead of fetching many rows.
		max_rows: rows to return, at most 200. Queries stop after 5 seconds.
		"""
		rows, more = await _read(_check(sql), max_rows=max(1, min(max_rows, MAX_ROWS)))
		text = json.dumps(rows, default=str, ensure_ascii=False)
		return text + (f"\n(only the first {len(rows)} rows; add a LIMIT or aggregate)" if more else '')

	return server
