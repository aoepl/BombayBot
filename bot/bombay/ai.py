# -*- coding: utf-8 -*-
from openai import AsyncOpenAI
from core.config import cfg

_client = None


def _get_client() -> AsyncOpenAI:
	global _client
	if _client is None:
		_client = AsyncOpenAI(api_key=cfg.OPENAI_API_KEY)
	return _client


async def generate_match_summary(match_text: str) -> str:
	resp = await _get_client().chat.completions.create(
		model="gpt-4o-mini",
		max_tokens=350,
		messages=[
			{
				"role": "system",
				"content": (
					"You are a hype commentator for a competitive pickup game community. "
					"Write a punchy 2-3 sentence pre-match preview based on the matchup provided. "
					"Call out key players by name, highlight the Elo gap if significant, "
					"and build excitement. Be opinionated — pick a favourite. "
					"Plain text only, no markdown, keep it under 350 characters."
				)
			},
			{"role": "user", "content": match_text}
		]
	)
	return resp.choices[0].message.content


async def generate_player_summary(stats_text: str) -> str:
	resp = await _get_client().chat.completions.create(
		model="gpt-4o-mini",
		max_tokens=450,
		messages=[
			{
				"role": "system",
				"content": (
					"You are a sharp, witty esports analyst for a competitive pickup game community. "
					"Write a punchy 3-5 sentence analysis of the player stats provided. "
					"Be specific with numbers, call out standout strengths and weaknesses, "
					"and give it personality — like a post-match breakdown. "
					"Keep it under 400 characters."
				)
			},
			{"role": "user", "content": stats_text}
		]
	)
	return resp.choices[0].message.content
