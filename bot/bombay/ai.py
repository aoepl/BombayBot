# -*- coding: utf-8 -*-
from google import genai
from core.config import cfg

_client = None


def _get_client() -> genai.Client:
	global _client
	if _client is None:
		_client = genai.Client(api_key=cfg.GOOGLE_AI_API_KEY)
	return _client


async def generate_match_summary(match_text: str) -> str | None:
	resp = await _get_client().aio.models.generate_content(
		model="gemma-4-31b-it",
		contents=(
			"You are a hype commentator for a competitive pickup game community. "
			"Write a punchy 2-3 sentence pre-match preview based on the matchup provided. "
			"Call out key players by name, highlight the Elo gap if significant, head to head and map based win rates, "
			"and build excitement. Be opinionated — pick a favourite. "
			"Plain text only, no markdown, keep it under 250 characters.\n\n"
			f"{match_text}"
		)
	)
	return resp.text


async def generate_player_summary(stats_text: str) -> str | None:
	resp = await _get_client().aio.models.generate_content(
		model="gemma-4-31b-it",
		contents=(
			"You are a sharp, witty esports analyst for a competitive pickup game community. "
			"Write a punchy 10-15 sentence analysis of the player stats provided. Be ruthless."
			"Be specific with numbers, call out standout strengths and weaknesses, "
			"and give it personality — like a post-match breakdown. "
			"Keep it under 1000 characters.\n\n"
			f"{stats_text}"
		)
	)
	return resp.text
