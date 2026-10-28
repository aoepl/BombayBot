# Available Commands

#### Queues
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /add                  | Join queues                                             |
| /remove               | Leave queues                                            |
| /who                  | List queue players                                      |
| /promote              | Promote a queue                                         |
| /server               | Show queue server setting                               |
| /maps                 | Show queue map list                                     |
| /map                  | Show random map from a queue map list                   |
| /auto_ready           | Confirm check-in automatically on next queue start      |
| /expire               | Show or set your current expire timer                   |

#### Matches
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /ready                | Confirm check-in                                        |
| /notready             | Decline check-in                                        |
| /capfor               | Become a captain                                        |
| /pick                 | Pick a player                                           |
| /subme                | Request a substitute                                    |
| /subfor               | Become a substitute                                     |
| /teams                | Show current teams                                      |
| /report               | Report match result                                     |

#### Personal settings
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /switch_dms           | Toggle DM notifications                                 |
| /expire_default       | Configure your default auto-remove behaviour            |
| /subscribe            | Subscribe to queue or channel promotion roles           |
| /unsubscribe          | Unsubscribe from queue or channel promotion roles       |
| /nick                 | Change your nick with rating prefix included            |

#### Stats
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /leaderboard          | Show rating leaderboard                                 |
| /rank                 | Show your or another player's rating stats              |
| /lastgame             | Show last played match                                  |
| /top                  | Show top active players on the channel                  |
| /stats show           | Show overall channel stats                              |
| /gossip               | Neutral AI summary of the disagreements in this channel over the last 1d/2d/3d/7d (optionally 2 more channels; bot messages and queue commands like ++ and -- are skipped; who reacted is looked up around the quarrels it finds, up to 50 requests): a neutral summary for people who missed it: who disagreed about what, each side's position, who weighed in or reacted, where it stands, in one paragraph of up to 100 words (no verdicts, insults never quoted), then up to 100 words of light banter about the spectators and the fun moments; once per 10 min per channel; only channels you can read |
| /gossip_optout        | Leave yourself out of /gossip completely (your messages, replies to you and messages mentioning you); run again to opt back in |
| /ask                  | Only for members who opted in with /ask_optin. Ask the AI a question about games, ratings or this server (e.g. "who has the best win rate on Arena this month?"); an AI agent looks it up itself through two MCP servers: the bot's database (read-only SELECT queries, 5 s each) and Discord (channels, messages from the last 30 days, members; people are matched to their database rows by Discord user id through their Discord names, queue nicks and in-game names; only public channels you can read, never private or restricted ones; only messages of members who ran /ask_optin, bots' messages left out); up to 10 steps, answer of up to 250 words; once every 30 s per member |
| /ask_optin            | Opt in to /ask, which only opted-in members can use: shows the risks first (your messages in public channels can be read by anyone's /ask, sent to Google's Gemini API and quoted, harshly, in public answers; opting out doesn't undo posted answers), then opt in with `confirm: I understand the risks`; run again to opt out. Your stats and names are used by /ask either way |

#### Game breakdowns (AoE2 replays)
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /aoe link             | Link an in-game name to your account (moderators: to anyone) |
| /aoe unlink           | Remove a linked in-game name                            |
| /aoe names            | Show the in-game names linked to a member               |
| /aoe stat             | A player's record from analysed games: flank/pocket, APM, results against Elo, best position and game plan (strategy + army style), individual and team luck |
| /aoe civs             | Civ win rates from analysed games, overall or for one map, best civs by Elo range, position and map |
| /aoe seats            | How many times each player was flank and pocket (win rate), optionally on one map or map type; with a player: their flank and pocket record by map and civ |
| /aoe draft            | Civ draft for your started match. mode team: 6 civs for each team to choose from, players decide who plays what and where; mode player: a civ and flank/pocket position for every player. 35-40 random civs are left out first. One draft per match (asking again shows it); moderators can redraw. Uses the map the queue drew (optional map when the match has none) |
| /aoe map_type         | Show a map's type (Open, Closed, Semi Closed, Hybrid, Nomad, Water) and where it came from; moderators: set it when the bot has it wrong |
| /replay auto_replay_off | Moderators: stop automatic game breakdowns of reported matches in this channel |
| /replay auto_replay_on  | Moderators: turn automatic game breakdowns back on                      |
| /replay auto_lookup_off | Moderators: post uploaded games without looking them up on World's Edge (no game links) |
| /replay auto_lookup_on  | Moderators: look up uploaded games again to link the download and match pages |
| /replay upload        | Analyse an attached recorded game (.aoe2record or .zip); `summary:False` only saves player stats. With a forum set as `replay_forum`, reported matches are also analysed automatically |

#### Miscellaneous
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /cointoss             | Flip a coin                                             |
| /help                 | Show channel or queue help                              |

#### Administration and Moderation
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /channel enable       | Enable the bot this channel                             |
| /channel disable      | Disable the bot this channel                            |
| /channel show         | Show channel configuration                              |
| /channel set          | Configure a channel variable                            |
|-                      |                                                         |
| /queue create_pickup  | Create a new pickup queue                               |
| /queue list           | List all queues on the channel                          |
| /queue show           | Show queue configuration                                |
| /queue set            | Configure a queue variable                              |
| /queue delete         | Delete a queue                                          |
| /queue add_player     | Add a player to queue                                   |
| /queue remove_player  | Remove a player from all or selected queue              |
| /queue clear          | Remove all players from all or selected queue           |
| /queue start          | Manually start a queue                                  |
| /queue split          | Split queue into multiple matches                       |
|-                      |                                                         |
| /match report         | Report an ongoing match result as a moderator           |
| /match create         | Manually create a rating match result                   |
| /match sub_player     | Forcefully swap one player with another                 |
| /match put            | Forcefully put a player in a team or unpicked list      |
|-                      |                                                         |
| /noadds list          | List banned players                                     |
| /noadds add           | Add a player to the ban list                            |
| /noadds remove        | Remove a player from the ban list                       |
|-                      |                                                         |
| /phrases add          | Add a player phrase on an add command                   |
| /phrases clear        | Clear player's phrases list                             |
|-                      |                                                         |
| /rating seed          | Set player rating and deviation                         |
| /rating penality      | Substract points from a player rating                   |
| /rating hide          | Hide a player from the leaderboard                      |
| /rating unhide        | Unhide a player from the leaderboard                    |
| /rating reset         | Reset channels rating data                              |
| /rating snap          | Snap players ratings to their rank minimum value        |
|-                      |                                                         |
| /stats show           | Show channel statistics                                 |
| /stats reset          | Reset all channel data except configs                   |
| /stats reset_player   | Reset a player statistics (including rating)            |
| /stats replace_player | Replace player1 with player2 in the database            |
