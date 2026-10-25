# -*- coding: utf-8 -*-
""" Build the Discord embed and the plain-text game summary from an analysis result. """
import re

from nextcord import Embed, Colour

from .analysis import mmss

SEAT_ORDER = {'Flank': 0, 'Pocket': 1, None: 2}
COLOR_EMOJI = {0: '🔵', 1: '🔴', 2: '🟢', 3: '🟡', 4: '🩵', 5: '🟣', 6: '⚪', 7: '🟠'}


EARLY_KILL = re.compile(r"'s .+ killed (one|\d+) of .+'s villagers")  # analysis.early_kill_moments


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
		+ (f" · {wl['count']} workers lost to enemies ({round(100 * wl['share'])}%)" if (wl := s.get('worker_losses')) and wl.get('count') else "")
	)


def _clip(text, limit):
	return text if len(text) <= limit else text[:limit - 1] + "…"


LINKS_FIELD = "Recorded game"


def links_value(links):
	""" "[Download recorded game](...) · [AoE2 Insights](...) · [AoE2 Companion](...)" or None. """
	if not links:
		return None
	parts = []
	if links.get('replay'):
		parts.append(f"[Download recorded game]({links['replay']})")
	if links.get('insights'):
		parts.append(f"[AoE2 Insights]({links['insights']})")
	if links.get('companion'):
		parts.append(f"[AoE2 Companion]({links['companion']})")
	return " · ".join(parts) or None


def with_links(embed, links):
	""" Add (or replace) the game links in an existing post's embed, just before the score table. """
	value = links_value(links)
	if not value:
		return embed
	fields = [(f.name, f.value, f.inline) for f in embed.fields if f.name != LINKS_FIELD]
	at = next((i for i, f in enumerate(fields) if f[0] == "Scores"), len(fields))
	fields.insert(at, (LINKS_FIELD, value, False))
	embed.clear_fields()
	for name, val, inline in fields:
		embed.add_field(name=name, value=val, inline=inline)
	return embed


def build_embed(result, title, narrative=None, footer=None, url=None, links=None):
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
		# Too long for one section: drop early villager kills (the smallest moments), latest first, before cutting
		# the big ones at the end.
		early = [i for i, (_, txt) in enumerate(result['moments']) if EARLY_KILL.search(txt)]
		while early and len("\n".join(lines)) > 1000:
			lines.pop(early.pop())
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
	if value := links_value(links):
		embed.add_field(name=LINKS_FIELD, value=value, inline=False)
	embed.add_field(name="Scores", value=score_table(result), inline=False)
	note = (
		"Scores are 1–10 relative to this lobby. Holding position: how a base held under attack, 7 when the enemy was kept out. "
		"Elo arrows compare each score with the player's BombayBot Elo."
	)
	embed.set_footer(text=_clip(f"{footer}\n{note}" if footer else note, 2048))
	return embed


def score_table(result):
	""" Monospace table of every player's scores, best overall first. """
	players, stats = result['players'], result['stats']
	order = sorted(players, key=lambda p: -stats[p]['scores']['overall'])
	fmt = lambda v: f"{v:>4.1f}" if v is not None else "   —"  # noqa: E731
	lines = ["Player        Exec Pres Hold Team Total  Mac  Mic W/L"]
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
				f"villagers queued {s['villagers_queued']} (queue clicks), estimated villagers and fishing ships alive "
				f"(fishing ships made {s.get('fishing_ships_made', 0)}) "
				f"{', '.join(f'{v} at {m} min' for m, v in s.get('villagers_at', {}).items())} (peak {s.get('villagers_peak')}, "
				f"about {s.get('villagers_lost')} lost); "
				f"unplanned floating resources {s['floating']} on average while spending {s.get('spend_per_min')} a minute ({s.get('float_minutes')} minutes of spending idle) (not counting near 200 population or saving "
				f"for a big purchase); trade carts {s['trade_carts']}; "
				f"{'ran out of gold income late (little gold spent, Market selling) and macro reduced ' + str(s['no_trade_penalty']) + ' for not making trade carts; ' if s.get('no_trade_penalty') else ''}"
				f"playstyle {s['playstyle']} (macro {s['macro']}/10: steady villager and military production, army size, "
				f"population kept high (estimated average {s.get('avg_pop')} of 200 from 20 min), economy; micro {s['micro']}/10 from fights: estimated enemy army value killed "
				f"{s['army']['value_killed_in_fights']} vs own army value lost {s['army']['value_lost_in_fights']}, "
				f"{round(s['army']['targeted_share'] * 100)}% of attack orders aimed at a specific target); "
				f"enemy villagers and fishing ships killed at least {s.get('villagers_killed', 0)} (only kills from attack "
				f"orders on them; kills by patrol, attack-move, towers or castles are not seen); army on the map from 8 min: {s['army']['avg_units']} units on average, peak {s['army']['peak_units']}, "
				f"average unit value {s['army']['unit_value']} (gold weighted most), army value sent at the enemy (its base, units or buildings) "
				f"{s['army']['value_sent']}; enemy macro disrupted {s.get('macro_disrupted', 0)} (resources of production that stalled while their army was in enemy bases); enemy Town Centers destroyed worth {s.get('buildings_destroyed', 0)}; pressure bonus +{s.get('age_delay_bonus', 0)} for enemy age-ups delayed by destroying the Town Center researching them; "
				f"{'forward castles/Donjons/towers worth ' + str(s['forward_value']) + ' Castles (cost-weighted), effect ' + str(s['forward_effect']) + ' of 1 (stood, held position, gained ground, covered siege pushes, denied enemy trade), pressure bonus +' + str(s.get('forward_bonus')) + '; ' if s.get('forward_effect') is not None else ''}"
				f"attacked units of 2+ enemies at once in {s.get('multi_enemy_minutes', 0)} minutes; "
				f"own units attacked by 2+ enemies at once in {s.get('focused_minutes', 0)} minutes"
				f"{' (traded ' + str(s['outnumbered_trade']) + ' of even in those minutes, 1 = even)' if s.get('outnumbered_trade') is not None else ''}"
				f" (execution bonus +{s.get('several_bonus', 0)} for fighting several enemies); tributes sent {s['trib_out']}, received {s['trib_in']}; APM {s['apm']}; "
				f"score {s['scores']['overall']}/10 (exec {s['scores']['exec']}, pressure {s['scores']['press']}, "
				f"holding position {s['scores']['surv'] if s['scores']['surv'] is not None else 'not scored'}, team {s['scores']['team']}); "
				f"base: {s['survival_note']}."
			)
	lines.append("Key moments: " + "; ".join(f"{t} {txt}" for t, txt in result['moments']))
	return "\n".join(lines)


# ---------- explain analysis: why each player got their scores (from what the analysis saved with the game)

SCORE_NAMES = dict(exec="Execution", press="Pressure", surv="Holding position", team="Team play")


def _fmt_score(v):
	return "—" if v is None else f"{v:g}"


def _parts_text(parts):
	""" One line per part: value → score (lobby range), with its share of the score. """
	live = [p for p in parts if p.get('score') is not None]
	total = sum(p['weight'] for p in live) or 1
	lines = []
	for p in parts:
		if p.get('score') is None:
			line = f"• {p['name']}: {p['value']} — {p.get('note', 'left out')}"
		else:
			line = f"• {p['name']} ({round(100 * p['weight'] / total)}%): {p['value']} → **{p['score']:g}**"
			if p['name'] == "Unplanned floating":
				line += " (minutes of spending sitting idle)"
			if p.get('range'):
				line += f" ({p['range']})"
			if p.get('note'):
				line += f"; {p['note']}"
		lines.append(line)
	return "\n".join(lines)


METHOD = [
	("Each part is scored 1–10 within this game", "The best player in the lobby on a part gets 10, the worst 1, and the "
		"rest in proportion. Army sent at the enemy is scored against the best only: the best gets 10 and the rest their "
		"share of it (half the best ≈ 5.5). Lower is better for age-up times, the first military unit and floating. A part nobody in "
		"the game did is left out and its weight spread over the rest."),
	("Execution: how well the player ran their game", "Weighted average of: **Castle Age click** (or, after an 8+ unit "
		"Feudal army, Feudal production uptime: Town Centers, Docks and military buildings kept busy); **Imperial Age "
		"click**; **villager value** (villagers and fishing ships made and kept alive, replacements after attacks "
		"counting extra); **first military unit** (a quarter weight); **unplanned floating** (minutes of the player's "
		"own spending sitting in the bank; saving for a 550+ purchase and being near 200 population don't count). "
		"Plus a bonus up to +1.5 for fighting several enemies at once: up to +0.75 for minutes attacking 2+ enemies' "
		"units, up to +0.75 for minutes attacked by 2+ enemies (by how well they traded), as a share of the game from 8:00."),
	("Pressure: how hard the player pushed the enemy", "Weighted average of: **army on the map** 35% (average army "
		"value from 8:00; units are scaled down when their damage was low both for their size, against the lobby "
		"median, and in total, against the lobby's most: the better of the two counts); **army "
		"sent at the enemy** 40% (each military unit ordered at an enemy unit, building or base counts once a minute, "
		"at the player's average unit cost, scaled the same way); **damage done** 15% (enemy army value killed in fights, 100 per "
		"villager killed, enemy Town Centers destroyed (their cost: research and queued units are refunded), and "
		"enemy macro disrupted: production that stalled while the army was in their base, "
		"against the 5 minutes before). Early army counts ×2 before 15:00 and ×1.5 before 20:00; gold counts 1.5×, stone 1.2×. Plus "
		"a bonus up to +1.5 for forward castles, Donjons and towers, by cost and effect (stood, held position, gained "
		"ground, covered siege pushes, denied trade), and up to +1 for each enemy age-up delayed by destroying its "
		"Town Center mid-research (verified by the age being clicked again; full at 4+ minutes lost)."),
	("Holding position: keeping the base", "A base the enemy never seriously attacked scores 7: the position was held "
		"and the fighting stayed elsewhere. An attacked base scores 1 + held × (4 + 5 × difficulty) + up to 1 for clearing "
		"enemy castles. **Held** is the share of new economy buildings "
		"kept at home after the attack began, less for Town Centers rebuilt elsewhere. **Difficulty** (0–1) is the "
		"enemy army and castles sent into the base compared with the most-attacked base, plus 0.05 a minute "
		"attacked by 2+ enemies at once (up to 0.3). **Workers lost** to enemies (villagers, fishing ships, trade carts) "
		"take 0.5 off for every 5% of the workforce lost before 40:00; after 40:00 only raids taking 20%+ of the "
		"workforce within a few minutes count, 0.5 per 5% of them. At most 3 off, never below 1."),
	("Team play: helping allies", "Weighted average of: **tributes sent** 50%; **army orders into allies' bases** 35% "
		"(defending them); **flares** 15%."),
	("Overall", "Flank: execution 27%, pressure 36%, holding position 27%, team play 10%. Pocket: 40%, 25%, 25%, 10%."),
]


def method_embed():
	""" How the scores are calculated (the same for every game). """
	e = Embed(title="How the scores are calculated", color=0x2f7d6d)
	for name, value in METHOD:
		e.add_field(name=name, value=value[:1024], inline=False)
	return e


def _sum_text(parts):
	""" "(a×w + b×w ...) ÷ total = x" for the parts that count. """
	live = [p for p in parts if p.get('score') is not None]
	total = sum(p['weight'] for p in live)
	if not total:
		return None, None
	value = sum(p['score'] * p['weight'] for p in live) / total
	terms = " + ".join(f"{p['score']:g}×{p['weight']:g}" for p in live)
	return f"({terms}) ÷ {total:g} = {value:.1f}", value


def explain_text(name, civ, ex):
	""" Why one player got their scores, as text for an embed description. """
	sc = ex['scores']
	w = ex['overall_weights']
	overall = " + ".join(f"{SCORE_NAMES[k].lower()} {_fmt_score(sc[k])} × {round(100 * v)}%" for k, v in w.items())
	out = [f"**Overall {_fmt_score(sc['overall'])}** = {overall} (weights for a {ex.get('seat') or 'player without a seat'}"
		+ (", holding position not scored" if sc['surv'] is None else "") + ")"]
	b = ex['bonuses']

	out.append(f"\n**Execution {_fmt_score(sc['exec'])}**\n" + _parts_text(ex['exec']))
	s = b['several']
	if s['value']:
		trade = f", traded {s['trade']:g}× an even trade when focused" if s.get('trade') is not None else ""
		out.append(f"• Bonus +{s['value']:g} for fighting several enemies: attacked 2+ enemies' units in {s['multi']} min, "
			f"attacked by 2+ enemies in {s['focused']} min{trade}")
	calc, _ = _sum_text(ex['exec'])
	if calc:
		out.append(f"= {calc}" + (f", + {s['value']:g} bonus" if s['value'] else "") + f" → **{_fmt_score(sc['exec'])}**")

	out.append(f"\n**Pressure {_fmt_score(sc['press'])}**\n" + _parts_text(ex['press']))
	f = b['forward']
	if f['count']:
		out.append(f"• Bonus +{f['value']:g} for forward buildings: {f['count']} in enemy bases ({f['built']} seen built), "
			f"worth {f['worth']:g} Castles by cost, effect {f['effect']:g} of 1 (stood, held position, gained ground, "
			f"covered siege pushes, denied trade)")
	ad = b.get('age_delay') or {}
	for d in ad.get('delays', []):
		out.append(f"• Bonus +{d['bonus']:g} for delaying {d['victim']}'s {d['age']}: clicked at {d['clicked']}, its Town Center "
			f"destroyed at {d['destroyed']}, clicked again at {d['again']} ({d['minutes']:g} min lost; full +1 at 4 min)")
	calc, _ = _sum_text(ex['press'])
	if calc:
		extra = (f['value'] or 0) + (ad.get('value') or 0)
		out.append(f"= {calc}" + (f", + {extra:g} bonus" if extra else "") + f" → **{_fmt_score(sc['press'])}**")

	sv = ex['surv']
	if sc['surv'] is None:
		out.append(f"\n**Holding position —** not scored: {sv['note']}, so its weight goes to the other scores")
	elif sv.get('untested'):
		if sv.get('worker_penalty'):
			out.append(f"\n**Holding position {_fmt_score(sc['surv'])}**\n• {sv['note']}\n= 7 for a held position − {sv['worker_penalty']:g} "
				f"for workers lost to enemies (0.5 per 5% of the workforce before 40:00; after it, per 5% of raids taking 20%+; at most 3) "
				f"→ **{_fmt_score(sc['surv'])}**")
		else:
			out.append(f"\n**Holding position {_fmt_score(sc['surv'])}**\n• {sv['note']}, so the base scores the set 7 for a held position")
	else:
		level = 'heavy' if sv['difficulty'] >= 0.66 else 'moderate' if sv['difficulty'] >= 0.33 else 'light'
		cleared = f", +{sv['cleared']:g} for clearing enemy castles" if sv.get('cleared') else ""
		out.append(f"\n**Holding position {_fmt_score(sc['surv'])}**\n• {sv['note']}\n• Held {round(100 * sv['held'])}% of the economy at "
			f"home ({round(100 * sv['home'])}% of new economy buildings, {sv['moved_tcs']} Town Centers elsewhere) under {level} "
			f"pressure (difficulty {sv['difficulty']:g} of 1){cleared}.")
		if sv.get('hold_credit'):
			out.append(f"• Held for {sv['hold_minutes']:g} minutes under pressure"
				+ (f" before the economy moved out at {sv['lost_at']}" if sv.get('lost_at') else "")
				+ f": +{sv['hold_credit']:g} (full credit from 10 minutes, for the part of the base that was lost).")
		raw = 1 + sv['held'] * (4 + 5 * sv['difficulty']) + (sv.get('cleared') or 0) + (sv.get('hold_credit') or 0)
		pen = sv.get('worker_penalty') or 0
		out.append(f"= 1 + {sv['held']:g} × (4 + 5 × {sv['difficulty']:g})" + (f" + {sv['cleared']:g}" if sv.get('cleared') else "")
			+ (f" + {sv['hold_credit']:g}" if sv.get('hold_credit') else "")
			+ (" (10 at most)" if pen and raw > 10 else "")
			+ (f" − {pen:g} for workers lost to enemies (0.5 per 5% of the workforce before 40:00; after it, per 5% of "
				f"raids taking 20%+; at most 3)" if pen else "")
			+ f" → **{_fmt_score(sc['surv'])}**" + (" (capped at 10)" if not pen and raw > 10 else ""))

	out.append(f"\n**Team play {_fmt_score(sc['team'])}**\n" + _parts_text(ex['team']))
	calc, _ = _sum_text(ex['team'])
	if calc:
		out.append(f"= {calc} → **{_fmt_score(sc['team'])}**")
	return "\n".join(out)


def explain_embeds(players):
	"""
	players: [(name, civ, seat, explain dict)] best overall first. How the scores are calculated first, then one
	embed per player, packed into messages
	under Discord's limits (10 embeds and 6000 characters per message). Returns a list of messages (lists of embeds).
	"""
	method = method_embed()
	messages, current, size = [], [method], sum(len(n) + len(v) for n, v in METHOD) + len(method.title)
	for name, civ, seat, ex in players:
		title = f"{name} ({civ}{', ' + seat if seat else ''}): {_fmt_score(ex['scores']['overall'])}"
		text = explain_text(name, civ, ex)[:4000]
		e = Embed(title=title[:256], description=text, color=0x2f7d6d)
		n = len(title) + len(text)
		if current and (size + n > 5800 or len(current) >= 10):
			messages.append(current)
			current, size = [], 0
		current.append(e)
		size += n
	if current:
		messages.append(current)
	return messages
