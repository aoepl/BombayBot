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


def player_line(result, pid):
	p, s = result['players'][pid], result['stats'][pid]
	sc = s['scores']
	ages = s['ages']
	seat = f" · {p['seat']}" if p['seat'] else ""
	rating = f" · TG {p['rating']}" if p.get('rating') else ""
	return (
		f"{COLOR_EMOJI.get(p['color_id'], '▪️')} **{p['name']}** · {p['civ']}{seat}{rating} · **{sc['overall']}**/10\n"
		f"> {s['strategy']}\n"
		f"> Castle {mmss(ages.get('Castle Age'))} · Imp {mmss(ages.get('Imperial Age'))} · {s['apm']} APM"
		f" · Exec {sc['exec']} · Pressure {sc['press']} · Survival {sc['surv']} · Team {sc['team']}"
	)


def _clip(text, limit):
	return text if len(text) <= limit else text[:limit - 1] + "…"


def build_embed(result, title, narrative=None, footer=None, url=None):
	won = [p for p in result['players'].values() if p['won']]
	colour = Colour.dark_teal() if won else Colour.dark_grey()
	embed = Embed(title=_clip(title, 256), colour=colour, url=url)
	desc = narrative or auto_summary(result)
	embed.description = _clip(desc, 3500)
	for idx, (_, members) in enumerate(_teams(result)):
		value = "\n".join(player_line(result, p) for p in members)
		embed.add_field(name=_team_label(result, members, idx), value=_clip(value, 1024), inline=False)
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
	embed.set_footer(text=_clip(footer or (
		"Scores are 1–10 relative to this lobby. Times are when age-ups were clicked; unit counts are queue clicks."
	), 2048))
	return embed


def auto_summary(result):
	players, stats = result['players'], result['stats']
	best = max(players, key=lambda p: stats[p]['scores']['overall'])
	press = max(players, key=lambda p: stats[p]['press_out'])
	hit = max(players, key=lambda p: stats[p]['press_in'])
	winners = [players[p]['name'] for p in players if players[p]['won']]
	parts = [f"Game length {mmss(result['duration'])}."]
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
	return " ".join(parts)


def text_for_ai(result, map_name=None, lobby=None):
	""" Compact plain-text version of the analysis to feed the recap model. """
	players, stats = result['players'], result['stats']
	lines = [f"Map: {map_name or 'unknown'}. Lobby: {lobby or 'unknown'}. Length: {mmss(result['duration'])}."]
	for idx, (_, members) in enumerate(_teams(result)):
		lines.append(_team_label(result, members, idx) + ":")
		for pid in members:
			p, s = players[pid], stats[pid]
			units = ", ".join(f"{n} x{c}" for n, c in s['units'].most_common(4))
			target = f", main target {s['top_target'][0]}" if s['top_target'] else ""
			lines.append(
				f"- {p['name']} ({p['civ']}, {p['seat'] or 'no seat'}): strategy {s['strategy']}; "
				f"Feudal {mmss(s['ages'].get('Feudal Age'))}, Castle {mmss(s['ages'].get('Castle Age'))}, "
				f"Imperial {mmss(s['ages'].get('Imperial Age'))}; units queued: {units}; "
				f"army orders into enemy bases {s['press_out']}{target}; enemy orders into own base {s['press_in']}; "
				f"tributes sent {s['trib_out']}, received {s['trib_in']}; APM {s['apm']}; "
				f"score {s['scores']['overall']}/10 (exec {s['scores']['exec']}, pressure {s['scores']['press']}, "
				f"survival {s['scores']['surv']}, team {s['scores']['team']})."
			)
	lines.append("Key moments: " + "; ".join(f"{t} {txt}" for t, txt in result['moments']))
	return "\n".join(lines)
