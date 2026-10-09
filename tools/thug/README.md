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

Format notes: [docs/thug/FORMATS.md](../../docs/thug/FORMATS.md).
