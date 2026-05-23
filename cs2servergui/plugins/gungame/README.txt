Gun Game (classic CS:GO-style progression)
==========================================

Repo:    https://github.com/ssypchenko/cs2-gungame
Release: https://github.com/ssypchenko/cs2-gungame/releases/latest

How to set this up
------------------
1. Download the latest cs2-gungame release ZIP.
2. Unzip it INTO THIS FOLDER (the one this README.txt is in).
3. After unzipping you should see:
     gungame/addons/counterstrikesharp/plugins/GunGame/GunGame.dll
   (the plugin folder name might be "GunGame", "cs2-gungame" or similar —
   if the deploy log says "missing GunGame.dll" rename to match)

Mode wiring
-----------
This plugin powers the new "Gun Game" mode added to GAME_MODES.

Note: This is DIFFERENT from CS2's built-in Arms Race mode. Gun Game gives
you the classic CS:GO progression (kill → next weapon → eventually knife
to win), while Arms Race is Valve's auto-progression variant.
