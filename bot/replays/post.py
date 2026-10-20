# -*- coding: utf-8 -*-
""" Build the Discord embed and the plain-text game summary from an analysis result. """
from nextcord import Embed, Colour

from .analysis import mmss

SEAT_ORDER = {'Flank': 0, 'Pocket': 1, None: 2}
COLOR_EMOJI = {0: '🔵', 1: '🔴', 2: '🟢', 3: '🟡', 4: '🩵', 5: '🟣', 6: '⚪', 7: '🟠'}


def _teams(result):
	players = result['players']
	teams = {}
	for pid, p in players.items():
		teams.setdefault(p['team_id'], []).append(pid)
	order = sorted(teams, key=lambda t: (not any(players[p]['won'] for p in teams[t]), t))
	return [(t, sorted(teams[t], key=lambda p: (SEAT_ORDER[players[p]['seat']], players[p]['name'].lower()))) for t in order]


def _team_label(result, members, idx):
	players = result['players']
	won = [players[p]['won'] for p in members]
	if all(w is True for w in won):
		return f"Team {idx + 1} · won"
	if all(w is False for w in won):
		return f"Team {idx + 1} · lost"
	return f"Team {idx + 1}"


VERDICT_MARK = {
	'well above their Elo': '⏫ well above Elo', 'above their Elo': '🔼 above Elo',
	'below their Elo': '🔽 below Elo', 'well below their Elo': '⏬ well below Elo',
}


def top_player(result, members):
	""" Highest BombayBot Elo on the team; best score when nobody has an Elo. """
	players, stats = result['players'], result['stats']
	with_elo = [p for p in members if players[p].get('elo') is not None]
	if with_elo:
		return max(with_elo, key=lambda p: players[p]['elo'])
	return max(members, key=lambda p: stats[p]['scores']['overall'])


def make_title(result, map_name=None):
	""" "Map - TopPlayer's Team (Civ) vs TopPlayer's Team (Civ)", Team 1 first. """
	players = result['players']
	sides = []
	for _, members in _teams(result)[:2]:
		top = players[top_player(result, members)]
		sides.append(f"{top['name']}'s Team ({top['civ']})")
	title = f"{map_name or 'Unknown map'} - " + " vs ".join(sides)
	return title[:100]


def player_line(result, pid):
	p, s = result['players'][pid], result['stats'][pid]
	sc = s['scores']
	ages = s['ages']
	seat = f" · {p['seat']}" if p['seat'] else ""
	elo = f" · Elo {p['elo']}" if p.get('elo') is not None else ""
	verdict = f" · {VERDICT_MARK[s['elo_verdict']]}" if s.get('elo_verdict') in VERDICT_MARK else ""
	return (
		f"{COLOR_EMOJI.get(p['color_id'], '▪️')} **{p['name']}** · {p['civ']}{seat}{elo} · **{sc['overall']}**/10{verdict}"
		f" · {s['playstyle']}\n"
		f"> {s['strategy']}\n"
		f"> Castle {mmss(ages.get('Castle Age'))} · Imp {mmss(ages.get('Imperial Age'))} · {s['apm']} APM"
		f" · Army {s['army']['avg_units']} avg / {s['army']['peak_units']} peak units"
	)


def _clip(text, limit):
	return text if len(text) <= limit else text[:limit - 1] + "…"


def build_embed(result, title, narrative=None, footer=None, url=None):
	won = [p for p in result['players'].values() if p['won']]
	colour = Colour.dark_teal() if won else Colour.dark_grey()
	embed = Embed(title=_clip(title, 256), colour=colour, url=url)
	desc = f"**AI summary**\n{narrative}" if narrative else auto_summary(result)
	embed.description = _clip(desc, 3500)
	for idx, (_, members) in enumerate(_teams(result)):
		# Discord allows 1024 characters per section: continue a team in a second section if needed.
		name, value = _team_label(result, members, idx), ""
		for line in (player_line(result, p) for p in members):
			if value and len(value) + len(line) + 1 > 1024:
				embed.add_field(name=name, value=value, inline=False)
				name, value = "\u200b", ""
			value += ("\n" if value else "") + _clip(line, 1024)
		embed.add_field(name=name, value=value, inline=False)
	if result['moments']:
		lines = [f"`{t}` {txt}" for t, txt in result['moments']]
		value = ""
		for line in lines:
			if len(value) + len(line) + 1 > 1000:
				value += "\n…"
				break
			value += ("\n" if value else "") + line
		embed.add_field(name="Key moments", value=value, inline=False)
	if result['tributes']:
		players = result['players']
		value = ", ".join(f"{players[a]['name']} → {players[b]['name']} {v:,}" for a, b, v in result['tributes'][:6])
		embed.add_field(name="Tributes", value=_clip(value, 1024), inline=False)
	embed.add_field(name="Scores", value=score_table(result), inline=False)
	note = (
		"Scores are 1–10 relative to this lobby. Survival is only scored when a base was seriously attacked (— otherwise). "
		"Elo arrows compare each score with the player's BombayBot Elo."
	)
	embed.set_footer(text=_clip(f"{footer}\n{note}" if footer else note, 2048))
	return embed


def score_table(result):
	""" Monospace table of every player's scores, best overall first. """
	players, stats = result['players'], result['stats']
	order = sorted(players, key=lambda p: -stats[p]['scores']['overall'])
	fmt = lambda v: f"{v:>4.1f}" if v is not None else "   —"  # noqa: E731
	lines = ["Player        Exec Pres Surv Team Total  Mac  Mic W/L"]
	for pid in order:
		p, sc = players[pid], stats[pid]['scores']
		name = p['name'][:12].ljust(12)
		result_txt = {True: "W", False: "L"}.get(p['won'], "-")
		lines.append(
			f"{name}  {fmt(sc['exec'])} {fmt(sc['press'])} {fmt(sc['surv'])} {fmt(sc['team'])}  {fmt(sc['overall'])}"
			f" {fmt(stats[pid]['macro'])} {fmt(stats[pid]['micro'])}  {result_txt}"
		)
	return _clip("```\n" + "\n".join(lines) + "\n```", 1024)


def auto_summary(result):
	players, stats = result['players'], result['stats']
	best = max(players, key=lambda p: stats[p]['scores']['overall'])
	press = max(players, key=lambda p: stats[p]['press_out'])
	hit = max(players, key=lambda p: stats[p]['press_in'])
	winners = [players[p]['name'] for p in players if players[p]['won']]
	parts = [f"Analysed up to the first resignation at {mmss(result['duration'])}."]
	if winners:
		parts.append(f"Won by {', '.join(winners)}.")
	parts.append(
		f"Top performer: {players[best]['name']} ({players[best]['civ']}) with {stats[best]['scores']['overall']}/10, "
		f"playing {stats[best]['strategy']}."
	)
	if stats[press]['press_out']:
		parts.append(
			f"Most aggressive: {players[press]['name']} ({players[press]['civ']}), "
			f"{stats[press]['press_out']} army orders into enemy bases."
		)
	if stats[hit]['press_in']:
		parts.append(f"Most pressured base: {players[hit]['name']} ({players[hit]['civ']}).")
	above = [players[p]['name'] for p in players if (stats[p].get('elo_verdict') or '').endswith('above their Elo')]
	below = [players[p]['name'] for p in players if (stats[p].get('elo_verdict') or '').endswith('below their Elo')]
	if above:
		parts.append(f"Played above their Elo: {', '.join(above)}.")
	if below:
		parts.append(f"Played below their Elo: {', '.join(below)}.")
	lost = [players[p]['name'] for p in players if stats[p].get('survival_note', '').startswith('main base lost')]
	if lost:
		parts.append(f"Lost their main base: {', '.join(lost)}.")
	return " ".join(parts)


def text_for_ai(result, map_name=None, lobby=None):
	""" Compact plain-text version of the analysis to feed the recap model. """
	players, stats = result['players'], result['stats']
	lines = [f"Map: {map_name or 'unknown'}. Lobby: {lobby or 'unknown'}. "
		f"Data covers the game up to the first resignation at {mmss(result['duration'])}; describe nothing after that."]
	for idx, (_, members) in enumerate(_teams(result)):
		lines.append(_team_label(result, members, idx) + ":")
		for pid in members:
			p, s = players[pid], stats[pid]
			units = ", ".join(f"{n} x{c}" for n, c in s['units'].most_common(4))
			target = f", main target {s['top_target'][0]}" if s['top_target'] else ""
			if p.get('elo') is not None:
				elo_txt = (f"BombayBot Elo {p['elo']} (Elo rank {s['elo_rank']} of {len(players)}, performance rank "
					f"{s['score_rank']}, performed {s['elo_verdict']}); ")
			else:
				elo_txt = f"no BombayBot Elo (performance rank {s['score_rank']}); "
			lines.append(
				f"- {p['name']} ({p['civ']}, {p['seat'] or 'no seat'}): {elo_txt}strategy {s['strategy']}; "
				f"Feudal {mmss(s['ages'].get('Feudal Age'))}, Castle {mmss(s['ages'].get('Castle Age'))}, "
				f"Imperial {mmss(s['ages'].get('Imperial Age'))}; units queued: {units}; "
				f"army orders into enemy bases {s['press_out']}{target}; enemy orders into own base {s['press_in']}, "
				f"enemy castles built in own base {s['enemy_castles_in_base']} (cleared {s['enemy_castles_cleared']}); "
				f"villagers queued {s['villagers_queued']} (includes replacements for killed villagers, not villagers alive); "
				f"unplanned floating resources {s['floating']} on average (not counting near 200 population or saving "
				f"for a big purchase); trade carts {s['trade_carts']}; "
				f"playstyle {s['playstyle']} (macro {s['macro']}/10: steady villager and military production, army size, "
				f"economy; micro {s['micro']}/10 from fights: estimated enemy army value killed "
				f"{s['army']['value_killed_in_fights']} vs own army value lost {s['army']['value_lost_in_fights']}, "
				f"{round(s['army']['targeted_share'] * 100)}% of attack orders aimed at a specific target); "
				f"army on the map from 15 min: {s['army']['avg_units']} units on average, peak {s['army']['peak_units']}, "
				f"average unit value {s['army']['unit_value']} (gold weighted most), army value sent at the enemy (its base, units or buildings) "
				f"{s['army']['value_sent']}; tributes sent {s['trib_out']}, received {s['trib_in']}; APM {s['apm']}; "
				f"score {s['scores']['overall']}/10 (exec {s['scores']['exec']}, pressure {s['scores']['press']}, "
				f"survival {s['scores']['surv'] if s['scores']['surv'] is not None else 'not scored'}, team {s['scores']['team']}); "
				f"base: {s['survival_note']}."
			)
	lines.append("Key moments: " + "; ".join(f"{t} {txt}" for t, txt in result['moments']))
	return "\n".join(lines)
