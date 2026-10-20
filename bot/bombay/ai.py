# -*- coding: utf-8 -*-
import asyncio

from google import genai
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
			"who carried and who struggled. Take each player's BombayBot Elo into account: say who performed better "
			"than their Elo and who fell short of it. Credit players who held their base under heavy pressure, "
			"and say plainly when a player lost their main base. Name players and civs, quote the timings and numbers given, "
			"and do not invent events, units or kills that are not in the data. "
			"Plain text only, no markdown, keep it under 1400 characters.\n\n"
			f"{game_text}"
		)
	)
