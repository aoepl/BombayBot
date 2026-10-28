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
### Player stats and civ statistics
Reports over the games analysed in the queue channel (uploads and automatic breakdowns):
* `/aoe stat [player]`: games and win rate; flank and pocket games with their win rates; average APM; how the player
  does against their BombayBot Elo (average difference and how often above or below); their best position and best
  game plan, each by average score over 2+ games. A game plan is the strategy (opening plus boom) and the army style
  it led to, judged as one pair, e.g. "Fast Castle, farm boom → Foot ranged". Army styles: Cavalry, Foot ranged
  (archers, crossbows, skirmishers, longbows ...), Cavalry archers, Infantry, Gunpowder, Monks, Naval: the army they relied on over every age they made one in (a Dark Age army
  of 3+ units, a Feudal army opening and the Castle Age army; siege is left out; a mixed early army takes the style of
  the line they carried on with, and when the ages differ the longest age decides); **individual luck**, the share of those bests they got
  per game; **team luck**, the share of their teammates' own bests the teammates got per game; most played civs.
* `/aoe civs [map]`: win rate of every civ, overall or on one map (name or type, e.g. `Arena`, `Golden Lakes`,
  `Closed`), and the best civs (2+ games) by BombayBot Elo range (under 1400, 1400–1599, 1600–1799, 1800+), by
  position (flank, pocket) and by map.
* `/aoe export [map] [days] [details]` (moderators): one JSON file of every analysed game in the channel, to give an
  AI, e.g. to ask which civs do best per map type, seat and Elo. Each game is a "card" with its players (civ, seat,
  team, result, Elo and the gap to it, strategy, unit lines, age-up times, villagers, scores and their parts, army
  and bonus figures), head-to-head results of direct opponents, lost bases and fights (units each side fielded and
  the result against Elo). The file starts with a short guide to the fields. `map` keeps one map or map type,
  `days` only recent games, `details` adds each player's full score breakdown. Bigger than 8 MB, it is gzipped.
* **Explain analysis**: right-click (or long-press) a game analysis post → Apps → Explain analysis. It works on
  the post or on any message in its thread, including in the replay forum. The bot replies, visible only to you,
  with why each player got each score:
  * first, how the scores are calculated: what each of execution, pressure, survival and team play measures and
    from which parts, how parts become 1–10 scores, the bonuses and the overall weights;
  * then for each player, the calculation: every part of execution, pressure, survival and team play, with the player's value, its 1–10 score, the
    lobby's best and worst, and its share of the score;
  * the bonuses (fighting several enemies, forward buildings) and any parts left out because nobody did them;
  * the arithmetic, for example "(8.3×1 + 10×1 + 1.8×1 + 5.7×0.25 + 10×1) ÷ 4.25 = 7.4, + 0.91 bonus → 8.3", and how
    the overall score weighs the four for the player's position.

  In a queue channel, replying `!explain` to the post does the same, sent to your DMs (in the channel if your DMs
  are closed). Games analysed before this was added need
  uploading again (they are re-analysed with the newer version).

### /ask: AI answers from the database and Discord
`/ask question` answers a question in plain words, e.g. "who has the best win rate on Arena this month?" or "what
did people say about the new map in #general this week?". An AI agent (Gemini, with function calling) looks the
answer up itself through two MCP servers, which run inside the bot (`bot/ask/`) and are opened for each question:
* **Database** (`sql_server.py`): `list_tables` (tables and columns) and `query` (one SELECT, WITH, SHOW, DESCRIBE
  or EXPLAIN query). Every query runs in a READ ONLY transaction, stops after 5 seconds and returns at most 200
  rows; locking, files, `SLEEP` and the system schemas are refused. For a second guard set `ASK_DB_URI` to a MySQL
  user that can only `SELECT` on the bot's database (otherwise `DB_URI` is used).
* **Discord** (`discord_server.py`): `list_channels`, `read_messages`, `search_messages` (last 30 days at most, 5,000
  messages read per question), `find_players` and `member_names`. Only text channels both the bot and the person
  asking can read are used, and bots' messages are left out.
* **Messages are opt-in**: the AI only gets the Discord messages of members who ran `/ask_optin` (run it again to
  opt out; saved in the `ask_optin` table). Everyone else's messages are skipped without their content being looked
  at, and the tools only tell the AI how many were left out. Opting in or not doesn't change the database side:
  stats, ratings and names are used for everyone. `/gossip_optout` only applies to /gossip.
* **Matching people to the database**: the database knows people by their Discord user id. `find_players` takes a
  name (or a user id or `<@id>`) and matches it against Discord user names, display names and nicks, and the
  database's queue nicks (`qc_players`), saved names (`players`), in-game names from analysed games
  (`aoe_profiles`) and linked in-game names (`aoe_name_links`). It returns one line per user id with all their
  names, their rating in this channel, roles and join date. `member_names` does the same for a list of user ids
  from query results, so people who have left the server still get a name. The agent is told to find a person's
  id this way first and query by id, never by name.

The agent gets at most 10 turns and answers in up to 250 words; it is told which queue and rating channel the
question comes from. Models are `GOOGLE_AI_AGENT_MODELS` (default `gemini-3.5-flash-lite,gemini-3.6-flash`: they
need function calling). Once every 30 seconds per member.

### Game breakdowns from recorded games
Members upload a recorded game with `/replay upload` (an .aoe2record file, or a .zip with one inside).
BombayBot analyses it, saves every player's performance (see Player insights) and posts a breakdown of the game.

Automatic breakdowns: when a queue channel's `replay_forum` is a **forum channel**, every match reported with `/report` is looked
up on World's Edge (the game's own match history, also used by AoE2 Insights and AoE2 Companion) about 90 seconds
after `/report loss` (or `/report draw` once both captains agree, or a moderator's `report_admin` result; never after
an abort), retrying for about 20 minutes. The bot searches the history of the highest-rated players whose
AoE2 profile it knows, downloads the highest-rated player's recording and posts the breakdown in the forum,
mentioning the players. It stops looking as soon as a member uploads that game with `/replay upload`. Profile ids
are learned from uploaded recordings: each recording lists every player's profile id, and all of them are saved with
the player's in-game name, against the member the name matched when there is one. A player whose name matched no
member is picked up as soon as the name is linked with `/aoe link`, so earlier uploads count. A reported match is
found once any one of its players has appeared in an uploaded game. When the search can't start, the queue
channel is told why: the replay channel isn't a forum channel, or none of the players has a known AoE2 profile yet
(then upload the game). When it gives up after 20 minutes, it asks for an upload. The log has a `Replay for match
<id>:` line for every step.

Posts never attach the recording, to save the server's Discord storage. They link to the official download
(`aoe.ms/replay`) and the match pages on AoE2 Insights and AoE2 Companion. The match id comes from the file name
(`AgeIIDE_Replay_<id>`, links straight away) or from a World's Edge lookup by players and start time, which
runs after the post is up and adds the links to it, so the post is never delayed.

Setup (optional):
* Uploading a game that is already posted updates that post in place (new Elo links, latest scoring) instead of posting again.
* Posts go to the queue channel. To post them in a forum instead (and turn on automatic breakdowns):
  `/channel set variable:replay_forum value:#game-breakdowns`. A text channel works too, without the automatic part.
* Turn the automatic part off (keeping the forum) with `/replay auto_replay_off`, back on with `/replay auto_replay_on`
  (moderators; stored as the `replay_auto` channel setting).
* Turn off the World's Edge lookup for uploads with `/replay auto_lookup_off`: uploaded games are then posted
  without the download and match links. Back on with `/replay auto_lookup_on` (channel setting `replay_lookup`).
* Set `GOOGLE_AI_API_KEY` for an AI-written recap; turn it off per channel with `replay_ai`.

What the breakdown shows:
* Key moments: the 5 biggest events, in time order, raids or fights between the same two players less than 5
  minutes apart are merged into one line with the time span: fights between two players (500+
  army value killed in 3 minutes, so a sea fight and a land fight at the same time stay apart), raids (3+
  villagers of one player killed by one raider in one burst, kills under a minute apart; events in the last 3
  minutes before the game is decided count half when picking), denied age-ups (an enemy Town Center destroyed while researching an age, clicked again later; always listed), castles destroyed, players eliminated (orders
  stop well before the end with no resignation, e.g. a regicide king killed) and the resignation that ends the analysis.
* Each player gets a strategy name: opening → Castle Age army → Imperial Age army → extras, for example
  "Fast Castle → Monks + Temple Guard → Imp: Monks + Spearmen/Halbs → 3 forward castles".
  Openings: "Feudal tower rush" (2+ towers in an enemy base before Castle Age), "Feudal archers + scouts"
  (8+ Feudal units, named by line, marked "(defensive)" if they never attacked), "Feudal boom" (fewer than 8
  Feudal military units, 2+ Feudal eco upgrades and 8+ farms or an extra Town Center in Feudal, e.g. Malay),
  "Fast Castle" (Castle Age clicked within 4 minutes of Feudal), "Feudal water: hulks + demo ships" when most Feudal units are warships,
  then the boom as its own stage, named by the economy behind it: "farm boom" (2+ Town Centers
  within 7 minutes of Castle Age), "fish boom" (5+ fishing ships that went to work, not left idle, up to
  10 minutes after Castle Age: made at a Dock whose gather point was on fish, or ordered onto fish) or "farm + fish boom" for both.
  Each age's army lists the two biggest unit lines plus any siege the player made (2 or more units).
* When the AI recap is on, the summary at the top is labelled "AI summary"; the plain summary has no label.
* Each player also gets four scores from 1 to 10, relative to the lobby: execution, pressure, survival and team play.
  A part nobody in the lobby did (no forward castles or towers, no tributes, nobody in Imperial Age) is left
  out and its weight spread over the other parts. Saving for an age-up or an expensive technology (550+
  resources; the 2 minutes before the click) is not a gap in military production. Execution weighs the first military unit at a quarter of the others.
  Overall weights: flanks execution 27%, pressure 36%, holding position 27%, team 10%; pockets 40/25/25/10;
  others 32/32/26/10.
  Execution: Castle and Imperial Age timing, villager value, first military unit and floating. A player who made
  8+ Feudal military units scores the better of their Castle Age click time and their Feudal production uptime
  (Town Centers and Docks kept busy, military buildings producing every minute), so a late Castle Age after a
  strong Feudal army is not penalised.
  Age-up timings are also judged against the player's direct opponent. In 4v4 that is by position: each flank
  against the enemy flank on its side, each pocket against the enemy pocket on its side. In other sizes it is the
  enemy they fought head-on, in either direction (20+ army orders into one's base, or 2+ villagers killed, by
  either side: an attacker and the player defending are direct opponents); with no such fight, the enemy whose
  base is nearest. When both were late (after the
  lobby's median click, or never), they were delaying each other, so Castle and Imperial Age timings are scored
  within the pair (5.5 when equal, higher for the faster) if that beats the lobby score. When the player aged up
  later than the opponent but the opponent lost their base and the player didn't, the late timing scores at least
  the opponent's.
  Execution also gets a bonus of up to +1.5 for fighting several enemies at once, from attack clicks on enemy
  units (villagers included):
  * up to +0.75 for minutes spent attacking units of 2 or more enemies (2+ clicks on each in that minute;
    early minutes count more);
  * up to +0.75 for minutes in which 2 or more enemies attacked the player's units, each minute counting by how
    the player traded in it. An even trade against two players earns full credit.

  Both are measured as a share of the game from 8:00, so long games don't hand everyone the full bonus.
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
  Villagers and fishing ships killed (eco kills): an enemy attack order targeted it and its owner never ordered it
  again (fishing ships only when their owner had ordered them onto fish, so a kill can be told from a survivor); hits in
  the last 3 minutes before the game is decided are not counted, since there is no time to see the villager
  again (villagers killed by attack-move, towers or castles are not seen either, so it is a lower bound).
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
  35% average army value on the map from 8:00 on, units scaled by how much damage they did, the better of two measures: for their size
  (full credit at the lobby median or better) and in total (against the lobby's most damage) (units plus castles, Kreposts, Donjons and towers), 40% army
  value sent at the enemy (scaled the same way, and scored against the game's best: the best gets 10 and
  the rest their share of it, so half the best is about 5.5) and 15% damage done (enemy army value killed in fights, enemy villagers killed at 100
  each, enemy Town Centers destroyed and enemy macro disrupted; see below), as shares of 90%, plus a bonus of up to +1.5 for forward castles, Donjons and towers (see below). The
  bonus only adds: players with no forward buildings, or ones that achieved little, lose nothing for it. An order counts as sent at the enemy
  when it targets an enemy unit or building, goes into an enemy base, or goes within 12 tiles of where an enemy
  army was ordered within a minute (fights in the open count too).
  Enemy Town Centers destroyed count their cost (research and queued units in them are refunded by the game). A
  Town Center destroyed with an age-up in progress (verified by the owner clicking the same age again later) also
  gives a pressure bonus of up to +1 per age delayed, in full when the age was delayed 4+ minutes from the first
  click to the click again, shared like the Town Center. A Town Center counts as destroyed when its owner never
  used it again and research was lost in it, enemies
  attack-clicked it 2+ times, or an enemy attack-clicked it with an enemy castle, Donjon or tower within 12 tiles
  (castle arrows can't be seen). Credit is shared by attack clicks, a nearby castle or tower counting as 5.
  Age-up times use the last click of each age, so an age lost with its Town Center counts from when it was clicked
  again.
  Enemy macro disrupted: for every attack on a base (minutes with 10+ enemy army orders into it, first 10 minutes),
  the defender's production uptime during it and the 2 minutes after is compared with the 5 minutes before (Town
  Centers and Docks busy and military queued every minute; military only once they have 120+ villagers). The drop
  × the attack's length × what the defender was spending a minute before is the production it cost them, credited
  to the attackers by their share of army orders into that base.
  Forward castles, Kreposts, Donjons and towers (built in an enemy base) count by cost and by what they did,
  and the bonus is full at 3 Castles' worth. A Castle is worth 1, a Donjon about 0.33 and a tower about 0.24. A building that achieved nothing still
  counts a quarter of its cost; the rest comes from its effect (0 to 1):
  * held, 25%: how long it stood, as a share of the rest of the game;
  * held position, 20%: after it went up, the owner's team's share of the activity within 15 tiles (buildings
    placed count 3, army orders 1);
  * gained ground, 15%: how much that share rose compared with the 5 minutes before;
  * covered a push, 20%: the owner's team attacking enemy buildings or using attack-ground (rams, trebuchets,
    bombards, mangonels) within 20 tiles in its first 15 minutes (full credit at 15 such orders);
  * denied trade, 20%: it stood within 10 tiles of the enemy team's trade route (the line between their
    Markets 40+ tiles apart) while they made trade carts (full credit at 30 carts). Left out when that team
    made no trade carts.

  Towers aren't tracked one by one, so they skip the held part. Castles are counted as buildings, not
  placements: repeated placements at the same spot within a minute count once, and placements never seen
  built (no units, research or enemy hits) count half. In army on the map a castle counts only while it
  stands, and the damage discount applies to units only (castle and tower arrows can't be seen as kills).
  Warships count for less in pressure where water does not decide the land fight: 0.4 of their value on lake
  maps (4 Lakes, Golden Lakes, Kawasan), 0.7 on coastal maps (Baltic, Mediterranean, Continental...), in full
  on island maps, 0.6 elsewhere. Micro still counts water fights at full value.
  Army on the map and army sent early count more: double before 15:00, 1.5 times before 20:00, and
  normal after that. Unit value is the resource cost,
  with gold weighted 1.5, stone 1.2, food and wood 1.0, so an expensive army kept topped up scores higher than
  a cheap one. Units are tracked by their ids in the player's commands (the replay has no kill data), and count
  as on the map until 90 seconds after their owner last commanded them.
  The overall score weighs them by seat (flank or pocket).
* Holding position (survival) is scored for every base. A base the enemy never seriously attacked scores 7 (the
  enemy was kept out). For a base that was seriously attacked: it rewards keeping the economy at
  home under pressure and marks players whose main base was destroyed. Holding a heavy attack scores higher
  than holding a light one. Enemy castles and towers built in a base count as pressure, weighted by how much
  enemy army came with them, and clearing them earns a little extra.  Being attacked by 2 or more enemies at once makes the test
  harder: each such minute adds to the attack's difficulty, up to 0.3.
* Every score is compared with the player's BombayBot Elo, so the post and the recap show who played above
  or below their Elo. The expected score is the lobby's average overall score plus the player's Elo standing times the
  spread (standard deviation) of the lobby's actual scores: one standard deviation above in Elo expects one
  standard deviation above in score. The standing is the average of two, in standard deviations of Elo: against
  the whole lobby and against their own team (their rank in it). Elos within 100 of each other count as the same level (players
  are grouped into tiers that share an average Elo), and the spread used is at least 100. The verdict is actual
  minus expected: +1.5 above their Elo, +3 well above, −1.5 below, −3 well below, otherwise around their Elo. Players are matched to BombayBot members by name. Names linked with `/aoe link` come
  first (a member can link several in-game names; moderators can link names for others). Otherwise the
  in-game name is compared with the member's BombayBot nick and Discord names, ignoring case, spaces,
  symbols and rating tags. Unmatched players show no Elo.
* Posts are titled "Map - Top player's Team (Civ) vs Top player's Team (Civ)", where the top player is the
  highest BombayBot Elo on each team and the winning team comes first.
* The breakdown covers the game up to the first resignation; the result still counts every resignation.
* With `summary:False`, `/replay upload` only saves the player stats and posts nothing.
* Each game is posted once. Uploading it again (even a different player's recording or a renamed file) analyses it
  again only when the bot's analysis has changed since (a newer version); the existing post and saved stats are
  then updated, and the reply says which version it was analysed with before. Player or Elo changes (for example a
  name linked with `/aoe link` afterwards) don't trigger a new analysis. Otherwise the reply links the post and nothing changes. If the post was
  deleted, uploading the game again analyses it again and creates a new post.

The analysis reads player commands, not kills or damage, so treat scores as a guide.

### Draft logic (not in Discord yet)
`bot/replays/draft.py` recommends civ picks and bans for a map, and plays out or rates a whole draft. Everything is
measured against Elo, so a civ is not rated well just because good players picked it:
* a game result is compared with the win chance the teams' Elos gave. The spread of Elos in that lobby sets how
  much a gap means: one standard deviation (at least 100) is a 62% favourite;
* a fight is compared with the share of army value killed that the Elos of the players in it predicted. Allies who
  joined the fight count, so one player holding two armies is judged 1 against 2.

Each option is rated 0–1 (0.5 = average) from:
* **Strength** of the civ in that seat, against Elo. Games on this map count fully, the same map type 0.5, the
  same seat elsewhere 0.2 and the other seat 0.1. Every estimate starts from 6 games of the tech tree's guess:
  how well the civ's best unit lines suit the seat (archers and infantry on the flank, cavalry in the pocket).
* **Army**: the unit lines the civ fields in that seat, taken from saved fights. The tech tree's best lines for
  the seat fill in until there are enough fights. Siege and monks count half.
* **Matchups** against the enemy civs: 60% their usual armies against each other, 40% how well each civ can
  switch to a counter for the other's army. Unit line matchups start from unit stats with each civ's own
  Blacksmith and armour upgrades (attack and bonus damage against armour classes, hit points, range, speed,
  splash, cost) and are corrected by real fights between those lines. Real fights between the two civs and lost
  bases correct them further; a base taken by a civ whose team still lost counts 1.5x. Same-seat enemies count
  fully, the other seat half. An enemy slot not picked yet counts as its 6 likeliest picks, at half weight.
* **Elo range fit** (how the civ does there compared with all its games), and **player fit** (how the player does
  with this civ compared with their other games).

The pick is the option with the best value plus half of what it denies the enemy. That is the enemy's best slot
value with the civ minus without it. `ban()` suggests bans (what the enemy would value most). `balance()` rates a
finished draft: the Elo win chance, the civ edge and both combined. `fair_first_pick()` gives first pick to the
team with the lower total Elo. Any format works: bans, pick order (A-B-A-B and A-B-B-A helpers), seats and
players with Elo. Chronicles civs are left out. Community votes (the civ voting sheet) can be added through
`Prior` later.

`/aoe draft` runs a whole draft for a started match, on the match's map, from the games analysed in that channel:
* First 35-40 random ranked civs are left out, so drafts differ from match to match.
* **mode team**: each team gets 6 civs to choose its players' civs from (one per seat, flanks at the ends and
  pockets between for 3+ players, then extra flank and pocket choices), judged at the team's average Elo; the
  players decide who plays which civ and where. Both modes say flank and pocket are only suggestions.
* **mode player**: every player gets a seat and a civ. Seats go where the players' own flank and pocket win rates
  (from `/aoe seats`, pulled towards 50% when they have few games) add up best; even splits are random. Picks then
  also count each player's own record with the civ.
* A snake draft (coin flip for first pick) finds strong civs for the map and seats: each pick is a random one of
  the civs within 2% of the best for the same seat (`PICK_SPREAD`), so the same strongest civs don't come up every
  time. They are then split
  between the teams, trying every split, so that both sides' civs are as even as possible; within a team every civ
  goes to the seat (and in player mode the player) it suits best, so a civ drafted for a flank can end up a pocket.
  In team mode each civ is shown with the seat it suits better on the map. On map types with their own seat rules
  (open, semi closed, hybrid) those rules replace the tech tree's generic seat fit. Elo is not evened out with
  civs (the rating change already accounts for it), so a higher rated team never gets weaker civs; Elo only
  shapes which civs suit each player. The post shows each pick, why it is in the draft, which team's civs are
  ahead and the civs left out.
* **Closed maps** favour civs that can field siege,
  gunpowder, towers and expensive power units. `fortress()` scores that from the tech tree: siege lines with Siege
  Engineers and Chemistry, gunpowder lines (unique ones too), Bombard Tower and tower upgrades, and the best
  expensive land unit; `CLOSED_BONUS` adds known civ bonuses the data can't show (Spanish, Turks, Bohemians,
  Hindustanis and Portuguese gunpowder; Celts, Mongols, Ethiopians, Koreans and Slavs siege).
* **Semi closed maps**: pockets want a food bonus, a cavalry bonus or a strong late game (the closed-map score);
  flanks want an aggressive civ: a strong archer line, a strong unique unit, or men-at-arms and towers play.
  `semi()` scores each part from the tech tree plus known bonuses (`FOOD_BONUS`, `CAV_BONUS`, `FLANK_BONUS`); a
  civ great at one part counts most; men-at-arms and towers counts 80%, as it works less often than a feudal
  archer plan. Archer lines missing Bracer, Thumb Ring or Arbalester are marked down
  (`KEY_ARCHER`).
* **Open maps**: flanks want top archer civs, pockets top cavalry civs, and both an eco bonus for a fast attack.
  `open_map()` scores archers (`archer_line` plus archer bonuses), melee cavalry (plus `CAV_BONUS`) and eco (early
  eco upgrades plus `ECO_BONUS`).
* **Hybrid maps** favour a strong navy and fishing (navy lines, Gillnets, Careening, Dry Dock, Shipwright) and
  wood (lumber upgrades), and a cavalry civ in the pocket that a fish boom pays for. `hybrid()` scores that per
  seat; `HYBRID_BONUS` adds known water and wood bonuses (Japanese, Malay, Italians, Vikings, Dravidians,
  Portuguese, Byzantines and Berbers water; Celts, Burmese, Huns and Mayans wood).
* The map is the one the queue drew for the match (the `map` option is only for queues without maps). Its type
  comes from a moderator's setting (`/aoe map_type`), else the built-in names (`insights.MAP_CATEGORIES`), else an
  online lookup: the AI model searches the map (Google Search where the model supports it) and the answer is saved
  in `aoe_map_types`, so each map is looked up once. Newly analysed games use the same map types.
* One draft per match: asking again shows the same draft, and moderators can redraw it (`redraw`).

Civ tech trees and unit stats come from `bot/replays/techtree.json`, built from the
[aoe2techtree](https://github.com/SiegeEngineers/aoe2techtree) data. `bot/replays/techtree.py` holds the unit lines,
line quality per civ (units and upgrades the civ has) and the stat-based counter estimate. Civ bonuses that are not
in the data, such as Franks cavalry hit points, only show through results.

### Player insights (saved for later)
Every recorded game uploaded with `/replay upload` is saved for
future features such as suggesting civs and seats for a map. Nothing reads it yet; it builds up as games are played.

* `aoe_games`: one row per game (map, map category, team size, length, who uploaded it).
* `aoe_fights`: one row per major fight between two players: both civs and seats, the players on each side
  (allies who joined), the units each side queued in the 5 minutes before and during it, army value each killed,
  and the share their Elos predicted.
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

* Lost bases are saved as matchups (table `aoe_matchups`): when a player's main base falls, the enemy with the
  most army orders into it (a castle built there counts as 50) is credited, as "winner civ + position took
  loser civ + position" on that map, with both players' member and Elo.
