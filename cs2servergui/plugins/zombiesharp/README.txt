ZombieSharp — Zombie Escape gamemode logic
==========================================

Repo:    https://github.com/oylsister/ZombieSharp
Release: https://github.com/oylsister/ZombieSharp/releases/latest

How to set this up
------------------
1. Download the latest ZombieSharp release ZIP.
2. Unzip it INTO THIS FOLDER (the one this README.txt is in).
3. After unzipping you should see:
     zombiesharp/addons/counterstrikesharp/plugins/ZombieSharp/ZombieSharp.dll
   (plus configs/, shared/, etc.)

How it fits with CS2Fixes
-------------------------
The Zombies mode now deploys TWO plugins together:
  - CS2Fixes (the existing "zombie" folder)  →  engine-level ZE fixes (MetaMod)
  - ZombieSharp (this folder)                →  actual ZE gamemode logic (CSS)

They are designed to be stacked — most CS2 Zombie Escape servers run both.

Verification
------------
After deploying, the diagnostic checks for:
  ✓ addons/counterstrikesharp/plugins/ZombieSharp/ZombieSharp.dll
