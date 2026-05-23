LiteMapChooser — end-of-map voting, RTV, nominate
==================================================

Repo:    https://github.com/PhantomYopta/LiteMapChooser
Release: https://github.com/PhantomYopta/LiteMapChooser/releases/latest
Current: 1.0.2

Lightweight map management plugin. Players can:
  !rtv      — rock-the-vote
  !nominate — nominate a map for the end-of-map ballot
  Vote menu auto-pops at end of map

The release ZIP ships the plugin under the folder name "RockTheVote" —
that's the actual CSS plugin folder name (NOT "LiteMapChooser" or
"MapChooser"). Don't rename it.

Wiring
------
Bundled into most modes' plugin lists automatically (Competitive, Casual,
Wingman, 3v3, 4v4, Deathmatch, Retakes, Zombies, Surf, KZ / Climb, and the
four fun modes). NOT included in 1v1, Practice, Jailbreak (those typically
don't rotate maps).

Customising the map list
------------------------
Edit:
  cs2servergui/plugins/mapchooser/LiteMapChooser/addons/counterstrikesharp/plugins/RockTheVote/maps.txt
…NOT the deployed copy in the CS2 server's csgo/ — that gets overwritten
on every deploy. The source folder is the source of truth.

ZIP layout (already extracted here)
-----------------------------------
  LiteMapChooser/addons/counterstrikesharp/plugins/RockTheVote/RockTheVote.dll
  LiteMapChooser/addons/counterstrikesharp/plugins/RockTheVote/maps.txt
