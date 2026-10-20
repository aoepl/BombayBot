# BombayBot
**BombayBot** is a Discord bot for pickup games in AOE Bombay discord lobby. BombayBot have a remarkable list of features such as rating matches, rank roles, drafts, map votepolls and more!

### Requirements
* **Python 3.9+** 
* **MySQL**.
* **gettext** for multilanguage support.

### Build
#### Create mysql user and database for BombayBot:
```
sudo mysql
CREATE USER 'pubobot'@'localhost' IDENTIFIED BY 'your-password';
CREATE DATABASE pubodb CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON pubodb.* TO 'pubobot'@'localhost';
```

#### Fill config file with your discord bot instance credentials and mysql settings and save.
```shell
cp config.example.cfg config.cfg
nano config.cfg
```

#### Docker commands
```shell
# Build and start in background
  docker compose down
  docker compose build --no-cache
  docker compose up -d
                                                                                                                                                                                                                         
  # View logs
  docker compose logs -f                                                                                                                                                                                                 
                                                                  
  # Stop
  docker compose down

  # Rebuild after code changes                                                                                                                                                                                           
  docker compose up -d --build
```
### Game breakdowns from recorded games
Members upload a recorded game with `/replay upload` (an .aoe2record file, or a .zip with one inside).
BombayBot analyses it, saves every player's performance (see Player insights) and posts a breakdown of the game.

Setup (optional):
* Uploading a game that is already posted updates that post in place (new Elo links, latest scoring) instead of posting again.
* Posts go to the queue channel. To post them in a forum instead: `/channel set variable:replay_forum value:#game-breakdowns`.
* Set `GOOGLE_AI_API_KEY` for an AI-written recap; turn it off per channel with `replay_ai`.

What the breakdown shows:
* Each player gets a strategy name: opening → Castle Age army → Imperial Age army → extras, for example
  "Fast Castle → Monks + Temple Guard → Imp: Monks + Spearmen/Halbs → 3 forward castles".
  Openings: "Feudal tower rush" (2+ towers in an enemy base before Castle Age), "Feudal archers + scouts"
  (8+ Feudal units, named by line, marked "(defensive)" if they never attacked), "Fast Castle" (Castle Age
  clicked within 4 minutes of Feudal), "Feudal water: hulks + demo ships" when most Feudal units are warships,
  plus "boom" for 2+ Town Centers soon after Castle Age.
  Each age's army lists the two biggest unit lines plus any siege the player made (2 or more units).
* When the AI recap is on, the summary at the top is labelled "AI summary"; the plain summary has no label.
* Each player also gets four scores from 1 to 10, relative to the lobby: execution, pressure, survival and team play.
  A part nobody in the lobby did (no forward castles or towers, no tributes, nobody in Imperial Age) is left
  out and its weight spread over the other parts. Saving for an age-up or an expensive technology (550+
  resources; the 2 minutes before the click) is not a gap in military production. Execution weighs the first military unit at a quarter of the others.
  Overall weights: flanks execution 27%, pressure 36%, survival 27%, team 10%; pockets 40/25/25/10; others
  32/32/26/10. When survival is not scored its weight is spread over the rest.
  Execution: Castle and Imperial Age timing, villager value, first military unit and floating. A player who made
  8+ Feudal military units scores the better of their Castle Age click time and their Feudal production uptime
  (Town Centers and Docks kept busy, military buildings producing every minute), so a late Castle Age after a
  strong Feudal army is not penalised.
* Villagers killed are estimated: a villager counted as killed is credited to the enemies whose army orders were
  near it, by share of those orders. They count towards micro (value killed) and pressure (damage done).
* Each player gets a playstyle: Macro, Micro, Macro + Micro, or "Neither stood out" (6.0 or more out of 10 earns
  a label). Macro: steady villager and military production, army size, population kept high from 20:00
  (estimated villagers alive, trade carts and army), villagers made and little unplanned floating, plus a
  bonus of up to 1 point for trade carts made and resources tributed to allies. Not trading only costs points
  (up to 1) when a player ran out of gold income late in a 40+ minute game: in the last 15 minutes analysed,
  gold was under 20% of their spending and they sold 1000+ at the Market or built no new Mining Camp (or Mule
  Cart / Muisca drop site), with no trade carts (20 remove the penalty) and no gold tributed in by allies. A
  player still mining gold is never penalised for not trading.
  Production steadiness (share of minutes with a worker or military unit queued) is scored on its own scale:
  86% of minutes scores 8.6. Worker production is half steadiness and half workers made per minute up to 10
  minutes after Castle Age, so producing from more Town Centers and Docks at once counts.
  Fishing ships count as economy workers alongside villagers (a Dock trains one every 40 seconds); warships
  count as army, and transport ships as neither.
  Villagers alive are estimated, since the replay has no unit counts: a queue click is 1 villager (5 with
  Shift); with several Town Centers selected each villager goes to the TC with the shortest queue, and a TC
  trains one every 25 seconds. A villager is counted as killed when its last order is followed within 3
  minutes by 3+ enemy army orders within 10 tiles of it; deleted villagers are subtracted; and villagers can
  never exceed 200 minus the army on the map.
  Villager value: each villager made counts in full while the player has under 120 alive, then less and less
  until 150, and nothing at 150+ (unless the base was attacked in the previous 10 minutes). After losses the
  alive count drops, so replacements count again.
  Floating is judged against spending: the unplanned bank divided by what the player spent per minute (units,
  research, buildings, tributes) from 10:00, so a big bank matters less for a player converting a lot into units.
  Floating: resources banked from 10 minutes on count against the player, except near 200 population
  (estimated villagers alive, army on the map and trade carts) and money saved for a research, age-up
  or building costing 550+ that the player starts within the next 2 minutes. Execution uses the same rules.
  Micro is about fights, not clicks, and is estimated because the replay has no kill data: a unit that stops
  being commanded during or right after a fight counts as lost there, and its value is credited to the enemies
  fighting that player at the time, split by how many orders each aimed at that player (an order at
  stacked armies is split between them). A drop-out only counts when its owner was clearly fighting (10+ army
  orders at enemies in a minute), since late-game units also go quiet when idle or garrisoned. Conversions
  look the same as kills and count in full. Micro weighs value traded (60%), how much fighting the player did (25%)
  and target picking (15%). The Micro label also needs more value killed than lost (1.1 times or better).
* Pressure measures the army a player keeps on the map and how much of it they send at the enemy:
  35% average army value on the map from 8:00 on, scaled by how much damage that army did for its size (full
  credit at the lobby median or better, none for an army that never did damage) (units plus castles, Kreposts, Donjons and towers), 40% army
  value sent at the enemy, 15% damage done (enemy army value killed in fights plus enemy villagers killed, 100
  each), and 10% forward castles and towers. An order counts as sent at the enemy
  when it targets an enemy unit or building, goes into an enemy base, or goes within 12 tiles of where an enemy
  army was ordered within a minute (fights in the open count too).
  Warships count for less in pressure where water does not decide the land fight: 0.4 of their value on lake
  maps (4 Lakes, Golden Lakes, Kawasan), 0.7 on coastal maps (Baltic, Mediterranean, Continental...), in full
  on island maps, 0.6 elsewhere. Micro still counts water fights at full value.
  Army on the map and army sent early count more: double before 15:00, 1.5 times before 20:00, and
  normal after that. Unit value is the resource cost,
  with gold weighted 1.5, stone 1.2, food and wood 1.0, so an expensive army kept topped up scores higher than
  a cheap one. Units are tracked by their ids in the player's commands (the replay has no kill data), and count
  as on the map until 90 seconds after their owner last commanded them.
  The overall score weighs them by seat (flank or pocket).
* Survival is only scored for players whose base was seriously attacked: it rewards keeping the economy at
  home under pressure and marks players whose main base was destroyed. Holding a heavy attack scores higher
  than holding a light one. Enemy castles and towers built in a base count as pressure, weighted by how much
  enemy army came with them, and clearing them earns a little extra. Players who were never attacked get
  "—" and their overall score uses the other three.
* Every score is compared with the player's BombayBot Elo, so the post and the recap show who played above
  or below their Elo. Players are matched to BombayBot members by name. Names linked with `/aoe link` come
  first (a member can link several in-game names; moderators can link names for others). Otherwise the
  in-game name is compared with the member's BombayBot nick and Discord names, ignoring case, spaces,
  symbols and rating tags. Unmatched players show no Elo.
* Posts are titled "Map - Top player's Team (Civ) vs Top player's Team (Civ)", where the top player is the
  highest BombayBot Elo on each team and the winning team comes first.
* The breakdown covers the game up to the first resignation; the result still counts every resignation.
* With `summary:False`, `/replay upload` only saves the player stats and posts nothing.
* Each game is posted once. Uploading it again (even a different player's recording or a renamed file) links
  the existing post and refreshes the saved stats. If the post was deleted, uploading the game again creates a new post.

The analysis reads player commands, not kills or damage, so treat scores as a guide.

### Player insights (saved for later)
Every recorded game uploaded with `/replay upload` is saved for
future features such as suggesting civs and seats for a map. Nothing reads it yet; it builds up as games are played.

* `aoe_games`: one row per game (map, map category, team size, length, who uploaded it).
* `aoe_game_players`: one row per player per game: AoE2 profile and the Discord member matched by name, map and map category
  (Open, Semi Closed, Closed, Hybrid, Nomad or Other), civ, seat (flank or pocket), win or loss, BombayBot Elo,
  strategy (opening, Feudal units and whether they attacked, forward towers, boom, Castle Age and Imperial Age
  armies, fast Imperial, forward castles, tower creep), age-up times, villagers queued, APM,
  the four scores, overall score, rank in the lobby, performance against Elo, pressure dealt and taken, and tributes.

The same game is never counted twice: re-analysing it replaces its rows. Games are matched by the game id in the file name (AgeIIDE_Replay_<id>), or by a fingerprint (start time, players, civs and teams). Example: best civs for one player on closed maps:

```sql
SELECT civ, seat, COUNT(*) games, AVG(won) win_rate, AVG(score_overall) avg_score
FROM aoe_game_players WHERE user_id = 123456789012345678 AND map_category = 'Closed'
GROUP BY civ, seat HAVING games >= 3 ORDER BY avg_score DESC;
```

## Credits
Developer: **Leshaka**. Contact: leshkajm@ya.ru.  
Used libraries: [discord.py](https://github.com/Rapptz/discord.py), [aiomysql](https://github.com/aio-libs/aiomysql), [emoji](https://github.com/carpedm20/emoji/), [glicko2](https://github.com/deepy/glicko2), [TrueSkill](https://trueskill.org/), [prettytable](https://github.com/jazzband/prettytable).

## License
Copyright (C) 2020 **Leshaka**.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 3 as published by the Free Software Foundation.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

See 'GNU GPLv3.txt' for GNU General Public License.
