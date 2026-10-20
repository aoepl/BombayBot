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
* Posts go to the queue channel. To post them in a forum instead: `/channel set variable:replay_forum value:#game-breakdowns`.
* Set `GOOGLE_AI_API_KEY` for an AI-written recap; turn it off per channel with `replay_ai`.

What the breakdown shows:
* Each player gets a strategy name: opening → Castle Age army → Imperial Age army → extras, for example
  "Fast Castle → Monks + Temple Guard → Imp: Monks + Spearmen/Halbs → 3 forward castles".
  Openings: "Feudal tower rush" (2+ towers in an enemy base before Castle Age), "Feudal archers + scouts"
  (8+ Feudal units, named by line, marked "(defensive)" if they never attacked), "Fast Castle" (Castle Age
  clicked within 4 minutes of Feudal), plus "boom" for 2+ Town Centers soon after Castle Age.
  Each age's army lists the two biggest unit lines plus any siege the player made (2 or more units).
* When the AI recap is on, the summary at the top is labelled "AI summary"; the plain summary has no label.
* Each player also gets four scores from 1 to 10, relative to the lobby: execution, pressure, survival and team play.
* Pressure measures the army a player keeps on the map and how much of it they send at the enemy:
  40% average army value on the map from 15 minutes on (units plus castles, Kreposts, Donjons and towers),
  45% army value sent into enemy bases, and 15% forward castles and towers. Unit value is the resource cost,
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
  armies, fast Imperial, forward castles, tower creep), age-up times, APM,
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
