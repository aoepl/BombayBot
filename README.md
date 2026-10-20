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
When a match is reported, BombayBot can find the game on World's Edge, download the recorded game
uploaded by the highest-rated player, and open a forum post with a breakdown of the game and every
player's performance. Players discuss the game in that post, and the recorded game is attached.

Setup:
1. Create a forum channel, then set it for the queue channel: `/channel set variable:replay_forum value:#game-breakdowns`.
2. Every player links their AoE2 account once: `/aoe link account:<profile id, Steam id or AoE2 Insights link>`.
3. Optional: set `GOOGLE_AI_API_KEY` for an AI-written recap; turn it off per channel with `replay_ai`.

How it works:
* After `/report`, the bot waits about 90 seconds, then searches the linked players' World's Edge match
  history for the game that finished closest to the report time. It keeps trying for about 20 minutes,
  because recorded games take a little while to appear.
* It downloads the recorded game of the player with the highest team-game rating and analyses it:
  age-up times, army composition, castles (went up, deleted or likely destroyed), pressure on each base,
  tributes and key moments.
* Each player gets a strategy name (for example "Fast Castle boom → Scouts/Hussars + Cavalry Archers")
  and four scores from 1 to 10, relative to the lobby: execution, pressure, survival and team play.
  The overall score weighs them by seat (flank or pocket).
* Survival is only scored for players whose base was seriously attacked: it rewards keeping the economy at
  home under pressure and marks players whose main base was destroyed. Holding a heavy attack scores higher
  than holding a light one. Enemy castles and towers built in a base count as pressure, weighted by how much
  enemy army came with them, and clearing them earns a little extra. Players who were never attacked get
  "—" and their overall score uses the other three.
* Every score is compared with the player's BombayBot Elo (the rating used to make the match), so the post
  and the recap show who played above or below their Elo.
* Posts are titled "Map - Top player's Team (Civ) vs Top player's Team (Civ)", where the top player is the
  highest BombayBot Elo on each team and the winning team comes first.
* `/replay upload` analyses any recorded game a member attaches. With `summary:False` it only saves the
  player stats (see Player insights) and posts nothing.
* The breakdown covers the game up to the first resignation; the result still counts every resignation.

The analysis reads player commands, not kills or damage, so treat scores as a guide.

### Player insights (saved for later)
Every analysed recorded game, whether found after a report or uploaded with `/replay upload`, is saved for
future features such as suggesting civs and seats for a map. Nothing reads it yet; it builds up as games are played.

* `aoe_games`: one row per game (map, map category, team size, length, source, linked pickup match).
* `aoe_game_players`: one row per player per game: AoE2 profile and linked Discord user, map and map category
  (Open, Semi Closed, Closed, Hybrid, Nomad or Other), civ, seat (flank or pocket), win or loss, BombayBot Elo,
  strategy (opening, boom, main units, fast Imperial, forward castles, tower creep), age-up times, APM,
  the four scores, overall score, rank in the lobby, performance against Elo, pressure dealt and taken, and tributes.

The same game is never counted twice: re-analysing it replaces its rows. Example: best civs for one player on closed maps:

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
