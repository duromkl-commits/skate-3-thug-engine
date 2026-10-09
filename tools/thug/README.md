# THUG level tools

Convert levels from your own Tony Hawk's Underground **PC** install into `.skate` maps.
No game data is included or committed.

```
pip install numpy
python tools/thug/thug_to_skate.py "C:\Games\Tony Hawk's Underground\Game" NJ maps/thug_nj.skate
```

Level names are the `Data/pre/<NAME>scn.pre` prefixes: NJ, NY, FL, SD, HI, VC, SJ, RU, SE, VN, HN, SC, SC2, PH, DJ, ...

What converts today: render geometry (first texture pass), textures, collision with THUG
terrain types mapped to Skate 3 rolling-sound surfaces, rails, and the Player 1 restart
as the spawn. Not yet: ladders, ledges, baked lighting, multi-pass materials.

Format notes: [docs/thug/FORMATS.md](../../docs/thug/FORMATS.md).
