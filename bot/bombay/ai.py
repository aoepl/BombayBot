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
	last_error = None
	for model in _models():
		for attempt in range(len(_RETRY_DELAYS) + 1):
			try:
				resp = await _get_client().aio.models.generate_content(model=model, contents=contents)
				return resp.text
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


async def find_quarrels(chat: str) -> list[tuple[int, int]]:
	""" The quarrels in a chat log (/gossip) as (first message #, last message #) pairs, at most 6. """
	import json
	import re
	answer = await _generate(
		(
			"Below is a Discord chat log; each line starts with '#number'. Find the quarrels: arguments, beefs, heated "
			"disagreements or trash talk between people (friendly banter with no real disagreement is not a quarrel). "
			"Answer with JSON only: a list of at most 6 [first message number, last message number] pairs, e.g. "
			"[[12, 30], [101, 110]], or [] when there are none.\n\n"
			f"{chat}"
		)
	)
	found = re.search(r'\[.*\]', answer or '', re.S)
	spans = []
	for pair in (json.loads(found.group(0)) if found else []):
		if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, int) for x in pair):
			spans.append((min(pair), max(pair)))
	return spans[:6]


async def generate_gossip(chat: str, where: str, when: str) -> str | None:
	""" A short, neutral summary of the disagreements in a channel's chat log (/gossip), for people who missed them,
	then a little friendly banter about the spectators and the light moments. """
	return await _generate(
		(
			"You summarise a gaming community's Discord (Age of Empires II pickup games) for members who missed the chat. "
			f"Below is the chat from {where} over {when}. Each line is '#number [time] Name (replying to #n Other): "
			"message [reactions]'; reactions show the emoji and who reacted (e.g. [😂 Morty, Orca +2]) or just a count "
			"(e.g. [😂×3]) when it wasn't looked up. Find the disagreements: arguments, disputes and heated exchanges "
			"between people.\n"
			"Start with a section headed **📰 What happened**: ONE paragraph of at most 100 words covering the disagreements, "
			"most significant first: who disagreed with whom and about what, each side's point put fairly, who else weighed "
			"in or reacted, and where it stands (resolved, agreed to disagree, or still open). Fewer details beat going "
			"over 100 words.\n"
			"Rules: be neutral and factual, like a news summary. Do not say who was right or wrong, do not take sides, do not "
			"joke or tease, and do not judge anyone's character. Use only names and events that appear in the chat and do not "
			"invent anything. Never quote insults, slurs or crude remarks in any language (including Hindi and Hinglish): "
			"say that someone made a personal remark or an insult instead. Leave out personal details (health, mental "
			"health, family, money). Friendly banter with no real disagreement is not a disagreement. If there were none, "
			"say so in one sentence in place of the paragraph.\n"
			"Then a section headed **🍿 From the stands**: at most 100 words of light, friendly banter "
			"about the spectators (who grabbed popcorn, piled on with reactions, chimed in from the side) and the light "
			"moments in the chat (jokes, puns, odd tangents, funny one-liners). Tease gently, like friends would. Here, and "
			"only here, humour is welcome. Call back the chat's own jokes, including cheeky puns and innuendo the members "
			"made themselves, even when they play on words from a disagreement (e.g. someone reading a sentence about it "
			"in a funny way): retell them playfully and keep it tame, never explicit, and don't invent new innuendo. Never "
			"mock anyone for their side of a disagreement or anyone who seemed upset, never take a side, and the rules on "
			"insults and personal details still apply. Leave the section out if there is "
			"nothing light to say. Discord markdown, no other sections.\n\n"
			f"{chat}"
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
