# -*- coding: utf-8 -*-
"""
Civ tech trees and unit lines, from techtree.json (built from SiegeEngineers/aoe2techtree data/data.json):
which units and technologies each civ has, and every unit's stats.

- line_of(unit name): the unit line a unit belongs to ("Knights", "Spearmen/Halbs", a civ's unique unit...).
- civ_line_quality(civ, line): 0..1, how complete the civ's version of the line is (its units, especially the
  final one, and the upgrades that line uses).
- counter(line_a, line_b): 0..1 chance a cost-for-cost army of line_a beats line_b, estimated from unit stats
  (attack and bonus damage against armour classes, hit points, range, splash). A starting point that real fight
  results adjust later.
Civ bonuses (e.g. Franks cavalry hit points) are not in the data, so they only show through results.
"""
import json
import math
import os
from functools import lru_cache

# Standard lines, base unit first, final upgrade last.
LINES = {
	'Archers': ['Archer', 'Crossbowman', 'Arbalester'],
	'Skirmishers': ['Skirmisher', 'Elite Skirmisher', 'Imperial Skirmisher'],
	'Cavalry Archers': ['Cavalry Archer', 'Heavy Cavalry Archer'],
	'Hand Cannoneers': ['Hand Cannoneer'],
	'Scouts/Hussars': ['Scout Cavalry', 'Light Cavalry', 'Hussar', 'Winged Hussar'],
	'Knights': ['Knight', 'Cavalier', 'Paladin'],
	'Camels': ['Camel Scout', 'Camel Rider', 'Heavy Camel Rider', 'Imperial Camel Rider'],
	'Elephants': ['Battle Elephant', 'Elite Battle Elephant'],
	'Steppe Lancers': ['Steppe Lancer', 'Elite Steppe Lancer'],
	'Militia line': ['Militia', 'Man-at-Arms', 'Long Swordsman', 'Two-Handed Swordsman', 'Champion'],
	'Spearmen/Halbs': ['Spearman', 'Pikeman', 'Halberdier'],
	'Eagles': ['Eagle Scout', 'Eagle Warrior', 'Elite Eagle Warrior'],
	'Monks': ['Monk'],
	'Mangonels': ['Mangonel', 'Onager', 'Siege Onager'],
	'Scorpions': ['Scorpion', 'Heavy Scorpion'],
	'Rams': ['Battering Ram', 'Capped Ram', 'Siege Ram'],
	'Bombard Cannons': ['Bombard Cannon'],
	'Galleys': ['Galley', 'War Galley', 'Galleon'],
	'Fire ships': ['Fire Galley', 'Fire Ship', 'Fast Fire Ship'],
	'Demo ships': ['Demolition Raft', 'Demolition Ship', 'Heavy Demolition Ship'],
	'Hulks': ['Hulk', 'War Hulk'],
	'Cannon Galleons': ['Cannon Galleon', 'Elite Cannon Galleon'],
}
# Upgrades each kind of line uses (Blacksmith, line techs, Monastery).
CAV_TECHS = ['Forging', 'Iron Casting', 'Blast Furnace', 'Scale Barding Armor', 'Chain Barding Armor', 'Plate Barding Armor',
	'Bloodlines', 'Husbandry']
INF_TECHS = ['Forging', 'Iron Casting', 'Blast Furnace', 'Scale Mail Armor', 'Chain Mail Armor', 'Plate Mail Armor', 'Squires',
	'Arson', 'Gambesons']
ARCHER_TECHS = ['Fletching', 'Bodkin Arrow', 'Bracer', 'Padded Archer Armor', 'Leather Archer Armor', 'Ring Archer Armor',
	'Thumb Ring', 'Ballistics']
CA_TECHS = ARCHER_TECHS + ['Parthian Tactics', 'Bloodlines', 'Husbandry']
MONK_TECHS = ['Redemption', 'Atonement', 'Sanctity', 'Fervor', 'Illumination', 'Block Printing', 'Theocracy', 'Faith']
SIEGE_TECHS = ['Chemistry', 'Siege Engineers']
NAVAL_TECHS = ['Careening', 'Dry Dock', 'Shipwright', 'Fletching', 'Bodkin Arrow', 'Bracer', 'Chemistry']
LINE_TECHS = {
	'Archers': ARCHER_TECHS, 'Skirmishers': ARCHER_TECHS, 'Cavalry Archers': CA_TECHS, 'Hand Cannoneers': ARCHER_TECHS + ['Chemistry'],
	'Scouts/Hussars': CAV_TECHS, 'Knights': CAV_TECHS, 'Camels': CAV_TECHS, 'Elephants': CAV_TECHS, 'Steppe Lancers': CAV_TECHS,
	'Militia line': INF_TECHS, 'Spearmen/Halbs': INF_TECHS, 'Eagles': INF_TECHS, 'Monks': MONK_TECHS,
	'Mangonels': SIEGE_TECHS, 'Scorpions': SIEGE_TECHS, 'Rams': SIEGE_TECHS, 'Bombard Cannons': SIEGE_TECHS,
	'Galleys': NAVAL_TECHS, 'Fire ships': NAVAL_TECHS, 'Demo ships': NAVAL_TECHS, 'Hulks': NAVAL_TECHS, 'Cannon Galleons': NAVAL_TECHS,
}
# Line names used in strategy names (analysis.LINE_NAMES) that differ from the ones here.
ALIASES = {'Fire Galley': 'Fire ships', 'Demolition Raft': 'Demo ships', 'Camel Rider': 'Camels'}
NOT_ARMY = {'Villager', 'Fishing Ship', 'Trade Cart', 'Trade Cog', 'Transport Ship', 'Petard', 'Siege Tower', 'Trebuchet',
	'Missionary', 'Flaming Camel', 'Traction Trebuchet', 'Mounted Trebuchet'}
HERO_COST = 800  # heroes (Sun Jian, Cao Cao, Liu Bei...) cost 500 food + 500 gold: not an army line
RESOURCE_WEIGHT = {'Food': 1.0, 'Wood': 1.0, 'Gold': 1.5, 'Stone': 1.2}
CAVALRY_CLASS, ARCHER_CLASS, INFANTRY_CLASS = '8', '15', '1'


@lru_cache(maxsize=1)
def data():
	with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'techtree.json'), encoding='utf-8') as f:
		return json.load(f)


def civs():
	return sorted(data()['civs'])


def unit(name):
	return data()['units'].get(name)


@lru_cache(maxsize=None)
def _standard():
	out = {}
	for line, units in LINES.items():
		for u in units:
			out[u] = line
	return out


@lru_cache(maxsize=None)
def unique_lines():
	""" {unit name: line name} for each civ's unique units (not in a standard line), elite folded into the base. """
	standard = _standard()
	counts = {}
	for c in civs():
		for u in data()['civs'][c]['units']:
			counts[u] = counts.get(u, 0) + 1
	out = {}
	for c in civs():
		for u in data()['civs'][c]['units']:
			if u in standard or u in NOT_ARMY or counts[u] > 3:
				continue
			base = u[6:] if u.startswith('Elite ') else u
			out[u] = base
	return out


def line_of(name):
	""" Line of a unit (or a line name passed through). """
	if name in LINES or name in unique_lines().values():
		return name
	name = ALIASES.get(name, name)
	return _standard().get(name) or unique_lines().get(name) or name


def civ_lines(civ):
	""" {line: [units the civ has in it]} including its unique units. """
	out = {}
	for u in data()['civs'].get(civ, {}).get('units', []):
		line = _standard().get(u) or unique_lines().get(u)
		if line:
			out.setdefault(line, []).append(u)
	return out


def civ_line_quality(civ, line):
	"""
	0..1: half from the line's units the civ has (the final one counts double), half from the upgrades that line
	uses. Unique lines count as complete in units. 0 when the civ doesn't have the line at all.
	"""
	have = set(data()['civs'].get(civ, {}).get('units', []))
	techs = set(data()['civs'].get(civ, {}).get('techs', []))
	if line in LINES:
		units = LINES[line]
		weights = [1] * (len(units) - 1) + [2]
		got = sum(w for u, w in zip(units, weights) if u in have)
		if not got:
			return 0.0
		unit_part = got / sum(weights)
		wanted = LINE_TECHS.get(line, [])
	else:  # unique line
		if not any(unique_lines().get(u) == line for u in have):
			return 0.0
		unit_part = 1.0
		u = _best_unit(civ, line)
		wanted = CA_TECHS if _is_cav_archer(u) else ARCHER_TECHS if _ranged(u) else CAV_TECHS if _cavalry(u) else INF_TECHS
	tech_part = sum(1 for t in wanted if t in techs) / len(wanted) if wanted else 1.0
	return round(0.5 * unit_part + 0.5 * tech_part, 3)


def _best_unit(civ, line):
	""" The most upgraded unit of the line the civ has (or the line's final unit when no civ is given). """
	if civ:
		have = data()['civs'].get(civ, {}).get('units', [])
		mine = [u for u in have if line_of(u) == line]
		if mine:
			order = LINES.get(line, [])
			return max(mine, key=lambda u: (order.index(u) if u in order else 0, u.startswith(('Elite ', 'Heavy '))))
	if line in LINES:
		return next((u for u in reversed(LINES[line]) if unit(u)), LINES[line][-1])
	return next((u for u, ln in unique_lines().items() if ln == line and u.startswith(('Elite ', 'Heavy '))),
		next((u for u, ln in unique_lines().items() if ln == line), line))


def _ranged(u):
	s = unit(u) or {}
	return (s.get('range') or 0) >= 2


def _cavalry(u):
	return CAVALRY_CLASS in (unit(u) or {}).get('armours', {})


def _is_cav_archer(u):
	return _ranged(u) and _cavalry(u)


def _cost(s):
	return sum(RESOURCE_WEIGHT.get(k, 1.0) * v for k, v in (s.get('cost') or {}).items()) or 50


def _hit(a, b):
	""" Damage one attack of unit a does to unit b: each attack class against b's armour in that class, at least 1. """
	dmg = 0
	for cls, amount in a.get('attacks', {}).items():
		if amount > 0 and cls in b.get('armours', {}):
			dmg += max(0, amount - b['armours'][cls])
	return max(1, dmg)


MELEE_CLASS, PIERCE_CLASS = '4', '3'
# Blacksmith and line upgrades: (tech, attack {class: +}, armour {class: +}, extra hp, extra range).
ARCHER_UPGRADES = [('Fletching', {PIERCE_CLASS: 1}, {}, 0, 1), ('Bodkin Arrow', {PIERCE_CLASS: 1}, {}, 0, 1),
	('Bracer', {PIERCE_CLASS: 1}, {}, 0, 1), ('Padded Archer Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 1}, 0, 0),
	('Leather Archer Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 1}, 0, 0),
	('Ring Archer Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 2}, 0, 0)]
MELEE_ATTACK = [('Forging', {MELEE_CLASS: 1}, {}, 0, 0), ('Iron Casting', {MELEE_CLASS: 1}, {}, 0, 0),
	('Blast Furnace', {MELEE_CLASS: 2}, {}, 0, 0)]
CAV_UPGRADES = MELEE_ATTACK + [('Scale Barding Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 1}, 0, 0),
	('Chain Barding Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 1}, 0, 0),
	('Plate Barding Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 2}, 0, 0), ('Bloodlines', {}, {}, 20, 0)]
INF_UPGRADES = MELEE_ATTACK + [('Scale Mail Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 1}, 0, 0),
	('Chain Mail Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 1}, 0, 0),
	('Plate Mail Armor', {}, {MELEE_CLASS: 1, PIERCE_CLASS: 2}, 0, 0)]
KITE_BONUS = 1.6  # a ranged unit faster than the melee unit chasing it keeps shooting while backing off


def upgraded(name, civ=None):
	""" The unit's stats with the Blacksmith/line upgrades its civ has (all of them when no civ is given). """
	s = unit(name)
	if not s:
		return None
	s = dict(s, attacks=dict(s.get('attacks', {})), armours=dict(s.get('armours', {})))
	techs = set(data()['civs'].get(civ, {}).get('techs', [])) if civ else None
	ranged, cav = (s.get('range') or 0) >= 2, CAVALRY_CLASS in s['armours']
	if not s['attacks']:
		return s
	ups = (ARCHER_UPGRADES + (CAV_UPGRADES[3:] if cav else [])) if ranged else CAV_UPGRADES if cav else INF_UPGRADES
	if ranged and PIERCE_CLASS not in s['attacks']:  # e.g. monks, siege: no Blacksmith attack
		ups = [u for u in ups if not u[1]]
	for tech, att, arm, hp, rng in ups:
		if techs is not None and tech not in techs:
			continue
		for k, v in att.items():
			if k in s['attacks']:
				s['attacks'][k] += v
		for k, v in arm.items():
			if k in s['armours']:
				s['armours'][k] += v
		s['hp'] = (s.get('hp') or 1) + hp
		if ranged and (s.get('range') or 0) >= 3:
			s['range'] = s['range'] + rng
	return s


def _power(a, b):
	""" Cost-for-cost fighting power of a against b (Lanchester square: numbers count twice). """
	dps = _hit(a, b) / max(0.5, a.get('reload') or 2)
	if (a.get('range') or 0) >= 3 and (b.get('range') or 0) < 2:
		dps *= 1.8  # free shots before melee units close in
		if (a.get('speed') or 0) > (b.get('speed') or 0):
			dps *= KITE_BONUS
	if a.get('blast') and CAVALRY_CLASS not in b.get('armours', {}):
		dps *= 1.8  # splash damage on clumped foot units
	return dps * (a.get('hp') or 1) / _cost(a) ** 2


@lru_cache(maxsize=None)
def counter(line_a, line_b, civ_a=None, civ_b=None):
	""" 0..1 chance a cost-for-cost army of line_a (civ_a's version) beats line_b (civ_b's). 0.5 = even. """
	a, b = upgraded(_best_unit(civ_a, line_a), civ_a), upgraded(_best_unit(civ_b, line_b), civ_b)
	if not a or not b:
		return 0.5
	ratio = _power(a, b) / max(1e-9, _power(b, a))
	r = math.sqrt(ratio)  # softened: real fights mix units, micro and upgrades
	return round(r / (1 + r), 3)
