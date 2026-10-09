# THUG level tools

Convert levels from your own Tony Hawk's Underground **PC** install into `.skate` maps.
No game data is included or committed.

```
pip install numpy
python tools/thug/thug_to_skate.py "C:\Games\Tony Hawk's Underground\Game" NJ maps/thug_nj.skate
```

Level names are the `Data/pre/<NAME>scn.pre` prefixes: NJ, NY, FL, SD, HI, VC, SJ, RU, SE, VN, HN, SC, SC2, PH, DJ, ...

What converts today: render geometry (first texture pass), textures, THUG's baked vertex
lighting (as a lightmap), the sky dome, collision with THUG terrain types mapped to Skate 3
rolling-sound surfaces, rails, and the Player 1 restart as the spawn. Objects hidden at
level start, trigger volumes and power-line rails are skipped (`--keep-wires` keeps the
wires). Not yet: ladders, ledges, real alpha blending (blended decals are cut out),
multi-pass materials.

To play a converted map in the release build, put the `.skate` file in
`<install>\data\installations\<id>\maps\` (the folder beside the installation's assets)
and pick it from the Escape menu.

## Skaters

```
python tools/thug/thug_skater_to_glb.py "C:\Games\Tony Hawk's Underground\Game" campbell kareem_campbell.glb
```

Writes a skinned GLB with Mixamo bone names (body + head from `skaterparts.pre`, bind
pose from `skeletons.pre`). Import it in game: Escape menu > Custom models > Import
model... The importer fits it to the Skate 3 rig. Names are the `skater_<name>` parts,
e.g. campbell, hawk, muska, mullen, lasek.

Format notes: [docs/thug/FORMATS.md](../../docs/thug/FORMATS.md).
