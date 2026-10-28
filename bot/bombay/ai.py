# -*- coding: utf-8 -*-
import asyncio

from google import genai
from google.genai import types
from core.config import cfg
from core.console import log

_client = None

# Errors worth retrying: the model is overloaded (503), rate limited (429) or briefly failing (500/504).
_RETRY_CODES = {429, 500, 503, 504}
_RETRY_DELAYS = (2, 6)  # seconds between attempts on the same model


def _get_client() -> genai.Client:
	global _client
	if _client is None:
		_client = genai.Client(api_key=cfg.GOOGLE_AI_API_KEY)
	return _client


def _models():
	""" Main model first, then the fallbacks, without duplicates. """
	names = [cfg.GOOGLE_AI_MODEL] + [m.strip() for m in cfg.GOOGLE_AI_FALLBACK_MODELS.split(',')]
	return list(dict.fromkeys(m for m in names if m))


def _error_code(e):
	code = getattr(e, 'code', None) or getattr(e, 'status_code', None)
	try:
		return int(code)
	except (TypeError, ValueError):
		for c in _RETRY_CODES:
			if str(c) in str(e)[:20]:
				return c
		return None


async def _generate(contents: str) -> str:
	""" Generate text, retrying busy models and falling back to the next model in the list. """
	resp, _ = await _generate_with(contents, _models())
	return resp.text


async def _generate_with(contents, models, config=None):
	""" (response, model that gave it), retrying busy models and falling back to the next model in models. """
	last_error = None
	for model in models:
		for attempt in range(len(_RETRY_DELAYS) + 1):
			try:
				resp = await _get_client().aio.models.generate_content(model=model, contents=contents, config=config)
				return resp, model
			except Exception as e:
				last_error = e
				code = _error_code(e)
				if code not in _RETRY_CODES:
					if code == 404:  # model not available to this key: try the next one
						break
					raise
				if attempt < len(_RETRY_DELAYS):
					await asyncio.sleep(_RETRY_DELAYS[attempt])
		log.info(f"AI model {model} unavailable ({last_error}); trying the next model.")
	raise last_error


async def generate_match_summary(match_text: str) -> str | None:
	return await _generate(
		(
			"You are a hype commentator for a competitive pickup game community. "
			"Write a punchy 5-10 sentence pre-match preview based on the matchup provided. "
			"Call out key players by name, highlight the Elo gap if significant, head to head and map based win rates, "
			"and build excitement. Be opinionated — pick a favourite. "
			"Plain text only, no markdown, keep it under 1000 characters.\n\n"
			f"{match_text}"
		)
	)


async def generate_player_summary(stats_text: str) -> str | None:
	return await _generate(
		(
			"You are a sharp, witty esports analyst for a competitive pickup game community. "
			"Write a punchy 10-15 sentence analysis of the player stats provided. Be ruthless."
			"Assume 50% winrate is at par"
			"Be specific with numbers, call out standout strengths and weaknesses, "
			"and give it personality — like a post-match breakdown. "
			"Keep it under 1000 characters.\n\n"
			f"{stats_text}"
		)
	)


async def generate_replay_summary(game_text: str) -> str | None:
	return await _generate(
		(
			"You are a caster recapping a finished Age of Empires II team game for the community that played it. "
			"Write a 6-10 sentence recap from the data provided: how each side played, the turning point, "
			"who carried and who struggled. Say whether each standout player won through macro (economy, production, army size) "
			"or micro (army control), as given in the data. Take each player's BombayBot Elo into account: say who performed better "
			"than their Elo and who fell short of it. Credit players who held their base under heavy pressure, "
			"and say plainly when a player lost their main base. Name players and civs, quote the timings and numbers given, "
			"and do not invent events, units or kills that are not in the data. "
			"Plain text only, no markdown, keep it under 1400 characters.\n\n"
			f"{game_text}"
		)
	)


MAP_TYPES = ('Open', 'Closed', 'Semi Closed', 'Hybrid', 'Nomad', 'Water')


async def classify_map(map_name: str) -> str | None:
	"""
	The map type of an Age of Empires II map (one of MAP_TYPES), looked up with Google Search when a model supports
	it, else from the model's own knowledge. None when no model answers with a type.
	"""
	prompt = (
		f"Age of Empires II: Definitive Edition map \"{map_name}\" (it may be a community or tournament map; version "
		"numbers and prefixes like nCZ_ are not part of the name). Which one of these types is it, for team games?\n"
		"Open: open land, no walls (e.g. Arabia, Golden Pit).\n"
		"Closed: players walled in by forest or walls from the start (e.g. Arena, Black Forest, Fortress).\n"
		"Semi Closed: partly closed bases, some open space (e.g. Palisade Arena, Hideout, Oasis).\n"
		"Hybrid: land with lakes or water where docks and fishing matter (e.g. Four Lakes, Golden Lakes).\n"
		"Nomad: no starting town center (e.g. Nomad, Land Nomad).\n"
		"Water: mostly water, decided by ships (e.g. Islands, Team Islands).\n"
		"Answer with the type only."
	)
	search = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
	answer = None
	for model in _models():
		try:
			resp = await _get_client().aio.models.generate_content(model=model, contents=prompt, config=search)
			answer = resp.text
			break
		except Exception as e:  # noqa: BLE001 - not every model can search: try the next one
			log.info(f"Map lookup with search on {model} failed ({e}).")
	if not answer:
		try:
			answer = await _generate(prompt)
		except Exception as e:  # noqa: BLE001
			log.info(f"Map lookup failed ({e}).")
			return None
	text = (answer or '').strip().lower()
	for t in sorted(MAP_TYPES, key=len, reverse=True):  # "semi closed" before "closed"
		if t.lower() in text:
			return t
	return None


AGENT_MAX_STEPS = 10         # model turns per question; the last one has to answer without tools
TOOL_RESULT_CHARS = 20_000   # one tool result sent back to the model is cut to this


def _agent_models():
	""" Models for /ask: they have to support function calling, so GOOGLE_AI_AGENT_MODELS rather than the main list. """
	return list(dict.fromkeys(m.strip() for m in cfg.GOOGLE_AI_AGENT_MODELS.split(',') if m.strip())) or _models()


async def _call_tool(client, name: str, args: dict) -> dict:
	""" One MCP tool call as a function response for the model: {'result': text} or {'error': text}. """
	try:
		result = await client.call_tool(name, args)
	except Exception as e:  # noqa: BLE001 - the model sees the error and can try something else
		return {'error': str(e)}
	text = '\n'.join(c.text for c in result.content if getattr(c, 'text', None))
	if len(text) > TOOL_RESULT_CHARS:
		text = text[:TOOL_RESULT_CHARS] + '\n(cut: ask for less)'
	return {'error': text} if result.is_error else {'result': text}


async def run_agent(question: str, instructions: str, clients) -> tuple[str, list[str]]:
	"""
	Answer question with the tools of the connected MCP clients (mcp.Client), calling them as the model asks, at
	most AGENT_MAX_STEPS turns. Returns (answer, names of the tools called in order).
	"""
	declarations, owner = [], {}
	for client in clients:
		for tool in (await client.list_tools()).tools:
			owner[tool.name] = client
			declarations.append(types.FunctionDeclaration(
				name=tool.name, description=tool.description or '', parameters_json_schema=tool.input_schema))

	def config(tools_allowed):
		return types.GenerateContentConfig(
			system_instruction=instructions,
			tools=[types.Tool(function_declarations=declarations)],
			automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
			tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
				mode='AUTO' if tools_allowed else 'NONE')),
		)

	contents = [types.Content(role='user', parts=[types.Part.from_text(text=question)])]
	models, used = _agent_models(), []
	for step in range(AGENT_MAX_STEPS):
		last = step == AGENT_MAX_STEPS - 1
		resp, model = await _generate_with(contents, models, config(not last))
		models = [model]  # stay on one model: its thought signatures are in the conversation
		calls = resp.function_calls
		if not calls:
			return resp.text or '', used
		contents.append(resp.candidates[0].content)
		parts = []
		for call in calls:
			used.append(call.name)
			if call.name in owner:
				response = await _call_tool(owner[call.name], call.name, dict(call.args or {}))
			else:
				response = {'error': f"No tool named {call.name}."}
			parts.append(types.Part.from_function_response(name=call.name, response=response))
		contents.append(types.Content(role='user', parts=parts))
	return '', used
