# THUG level formats → `.skate` converter notes

Working notes for the THUG → Skate 3 Rust Engine level converter. Everything
under "THUG formats" is read from the THUG source
(github.com/RetailGameSourceCode/TonyHawksUnderground, Xbox loaders) and
**confirmed against the PC release**: the Beenox port (Direct3D 9) ships the Xbox
formats (`*.xbx`) inside LZSS `.pre` archives in `Data/pre/`. The parsers in
`tools/thug/` read New Jersey and the NJ skateshop end to end with no trailing bytes.

Tools: `thug_pre.py` (archives), `thug_level.py` (scene, collision, textures),
`thug_to_skate.py` (writes SKATE09).

## Conventions

- Little-endian, `bool` = 1 byte, no padding inside records unless noted.
- Units are inches (collision uses 1/16-inch fixed point). `.skate` is meters:
  scale by 0.0254.
- Billboard data has Z negated on load; the rest of the geometry is read
  as-is. Handedness against Bevy still has to be checked on a real level.
- Names are CRC32 checksums (Neversoft's `Crc::GenerateCRCFromString`), not
  strings. Real names have to come from the level's script data.

## THUG formats

PC install: `Data/pre/<LEVEL>scn.pre` holds `levels/<level>/<level>.scn.xbx`
(render geometry + materials) and `.tex.xbx` (textures); `<LEVEL>col.pre` holds
`.col.xbx` (collision); `<LEVEL>.pre` holds the level's `.qb` scripts (node array:
rails, ladders, ledges, spawns, goals), cameras and terrain sounds.

### `.pre` archives (Sys/File/PRE.cpp)

```
u32 total_size, u32 version (0xABCD0003), u32 num_files
per file: i32 size, i32 compressed_size (0 = stored), i16 name_len, u16 pad,
          u32 name_checksum, char name[name_len], data, padded to 4 bytes
```
LZSS: 4096-byte ring buffer pre-filled with spaces, start 4078, flag byte per 8
items (1 = literal), back-reference = 12-bit offset + 4-bit length + 3.

### `.tex.xbx` (Gfx/XBox/p_nxtexture.cpp)

```
u32 version, u32 num_textures
per texture:
  u32 checksum, width, height, levels, texel_depth, palette_depth, dxt, palette_size
  u8  palette[palette_size]            (if palette_size > 0)
  per mip level: u32 size, u8 data[size]
```
Format: dxt 1/2 → DXT1, dxt 5 → DXT5, else texel_depth 8 → P8 (palette),
16 → A1R5G5B5, 32 → A8R8G8B8. NJ on PC: 454 DXT5, 345 DXT1, 1 uncompressed
32-bit (treated as linear, unverified).

### `.scn.xbx` (Gfx/XBox/p_nx.cpp `s_plat_load_scene`)

```
u32 mat_version, mesh_version, vert_version
materials (below)
i32 num_sectors
sector × num_sectors (below)
i32 num_hierarchy_objects, CHierarchyObject[...]   (Gfx/NxHierarchy.h)
```

**Materials** (NX/material.cpp `LoadMaterials`):
```
u32 count
per material:
  u32 checksum, name_checksum, passes, alpha_cutoff
  bool sorted; f32 draw_order; bool single_sided; bool no_bfc; i32 zbias
  bool grassify; if grassify: f32 grass_height, i32 grass_layers
  f32 specular_power; if > 0: f32 specular_rgb[3]
  per pass:
    u32 texture_checksum, flags
    bool has_color; f32 color[3]          (0.5 = neutral)
    u64 reg_alpha                          (low 24 bits blend mode, high 32 fixed alpha /128)
    u32 u_address, v_address; f32 envmap_tiling[2]; u32 filtering
    if flags & 0x1    (UV wibble):            f32[8]
    if pass 0 && flags & 0x2 (VC wibble):     u32 nseq; per seq: u32 nkeys, i32 phase, {i32 time, u8 rgba[4]}[nkeys]
    if flags & 0x800  (texture animates):     i32 nkeys, period, iterations, phase; {u32 time, u32 tex_checksum}[nkeys]
    u32 mmag, mmin, k(f32 bits), l        (always present, 16 bytes)
```

**Sector** (p_nxsector.cpp `CXboxSector::LoadFromFile`):
```
u32 checksum; i32 bone_idx; i32 flags; u32 num_mesh
f32 bbox[6]; f32 bsphere[4]
if flags & 0x800000 (billboard): u32 type; f32 origin[3], pivot_pos[3], pivot_axis[3]
i32 num_vertices; i32 stride
f32 pos[3] × n
if flags & 0x04:  f32 normal[3] × n
if flags & 0x10:  u32 weight × n; u16 bone_idx[4] × n
if flags & 0x01:  i32 num_tc_sets; f32 uv[2*num_tc_sets] × n
if flags & 0x02:  u32 color (D3DCOLOR ARGB, baked lighting) × n
if flags & 0x800: u8 vc_wibble_index × n
per mesh:
  f32 center[3], radius, bbox_min[3], bbox_max[3]
  u32 flags; u32 material_checksum; u32 num_lods
  per lod: i32 num_indices; u16 indices[num_indices]
```
Index lists are triangle strips with degenerate stitches (confirmed on NJ).
Only LOD 0 is needed.

### `.col.xbx` (Gfx/NxScene.cpp, Gel/Collision/CollTriData.h)

```
header (32 bytes): i32 version, num_objects, total_verts,
                   faces_large, faces_small, verts_large, verts_small, pad
object × num_objects (64 bytes each):
  u32 checksum; u16 flags; u16 num_verts; u16 num_faces
  u8 use_face_small; u8 use_fixed_verts
  u32 face_offset                       (bytes, from face base)
  f32 bbox_min[4], bbox_max[4]
  u32 vert_offset                       (bytes, from vert base)
  u32 bsp_offset; u32 intensity_index; u32 pad
align 16 → vert base:
  float verts: f32[3] (12 B); fixed verts: u16[3] (6 B) = bbox_min + v/16
intensity: u8 × total_verts
align 4 → face base:
  large face (10 B): u16 flags, u16 terrain, u16 idx[3]
  small face (8 B):  u16 flags, u16 terrain, u8 idx[3], u8 pad
i32 bsp_size; BSP nodes; face index lists   (not needed, the engine builds its own)
```
Face flags we care about (Gfx/nxflags.h): 0x1 skatable, 0x2 not skatable,
0x4 wallride, 0x8 vert, 0x10 non-collidable (drop), 0x40 trigger (fires a script,
e.g. a gap, but **is still solid**: most quarter pipes carry it), 0x1000 invisible.

## `.skate` target (crates/skate-data/src/skate_map.rs)

There's a minimal writer example in `tools/make_skate_demo.py` (v8). Collision
triangles carry `surface`: the low 7 bits are the **audio tag**, and bits 7–11
are the **physics type**. Rolling sounds come from the audio tag
(`crates/skate-game/src/game_audio/grain_bed.rs::grain_for`).

## Terrain → Skate audio tag (first pass)

Skate 3 only has rolling sounds for asphalt, concrete, wood and metal, plus a
few special layers. Several THUG terrains have **no real Skate equivalent**
and get the closest match.

| THUG terrain | Skate tag | Skate sound |
|---|---|---|
| DEFAULT, ASPHALT | 1 | asphalt_smooth |
| CONCSMOOTH, SIDEWALK, TILE, WETCONC, PLEXIGLASS, FIBERGLASS, METALFUTURE | 3 | concrete_smooth |
| CONCROUGH, BRICK, ROCK | 4 | concrete_rough |
| GRAVEL, DIRTPACKED | 5 | concrete_aggregate (approximation) |
| WOOD, WOODMASONITE, WOODPLYWOOD, WOODFLIMSY, WOODSHINGLE, WOODPIER | 6 | wood_ramp |
| METALSMOOTH, METALROUGH, METALCORRUGATED, METALGRATING, METALTIN, CHAINLINK, METALFENCE, CONVEYOR | 9 | metal_smooth |
| GRASS, GRASSDRIED, DIRT, SAND, SNOW | 5 | concrete_aggregate (stand-in until tags 8/10/67–70 are identified) |
| ICE, CARPET | 3 | concrete_smooth (stand-in) |
| WATER | 1, physics type 12 | Skate's water physics |
| GRIND* (rails) | per rail | rail sound comes from the rail, not the face |

Physics type: 1 (ordinary ground) everywhere except water (12). Other values
(6 unrideable, 7 don't-align, 8 …) exist in the engine but aren't mapped yet.

## Level scripts (`<level>.qb`, Gel/Scripting/tokens.h, skiptoken.cpp)

Compiled QB is a token stream: 1-byte token, then operands. Name, integer, float,
line number, jump: 4 bytes. Vector: 12 bytes, pair: 8, string: u32 length + bytes.
A trailing table of `CHECKSUM_NAME` tokens (0x2B: u32 checksum + C string) names
most checksums. Checksums are Neversoft CRC32: lower-cased, `/` to `\`, init
0xFFFFFFFF, **no final xor**.

`NodeArray` is an array of structs. Most nodes are compressed: a bare flag such
as `ncomp_Waypoint_3239` names a global struct whose fields (Class, Type,
TerrainType...) belong to the node. On NJ: 1783 RailNode, 167 ClimbingNode
(155 Ledge, 12 Ladder), 14 Restart (one `Type = Player1`). Rails are chains through
`Links` (node indices), giving 284 rails on NJ. `thug_qb.py` reads it.

## Still to work out

- In-game check of orientation (Z is negated, winding reversed; collision floors
  come out facing up) and texture V direction.
- Face flags on PC: almost every face carries 0x2000; 0x1 (skatable) is unused.
- Ladders and ledge hang points (ClimbingNode) aren't exported yet.
- Spawn heading is the restart's yaw negated for the Z flip; check in-game.
- Baked vertex colours are written as a lightmap: one 2x2 texel cell per triangle
  (A, B, C, B+C−A), lightmap UVs at the texel centres, values stored as
  sqrt(colour/128) because the world shader squares the lightmap.
- Material passes flagged 0x8 (environment map) are skipped; the first other pass is
  used. Blended decals with near-black colour (baked shadows) are dropped.
- Blending: portable `.skate` materials output alpha 1 even when `alpha_mode` is 2,
  so THUG BLEND passes are exported as alpha cutouts and add/subtract/modulate
  passes are dropped. Proper blending needs SKATE12 retail material definitions
  (family 7 reads texture alpha), which also switch the engine into its retail
  scene mode; not tried yet.
- Visibility: only nodes with `CreatedAtStart` are exported; ProximNode,
  EmitterObject, GameObject and `Occlusion*` objects are dropped (NJ: 330 of 1700
  collision objects). Power-line rails (GRINDELECTRICWIRE/GRINDWIRE) are dropped
  unless `--keep-wires`.
- Sky: `levels/<l>_sky/<l>_sky.scn.xbx` is exported as a static dome scaled to a
  650 m radius around the level centre (the engine's own sky is a flat clear colour).

## Skaters (`skaterparts.pre`, `skeletons.pre`)

`models/skater_male/skater_<name>.skin.xbx` and `head_<name>.skin.xbx` are ordinary
`.scn.xbx` scenes whose sectors carry flag 0x10: per vertex a u32 of packed weights
(11:11:10 bits, /1023, /1023, /511) and u16 bone indices[4] (first three used).
Positions are model space in the bind pose. Textures are in the matching `.tex.xbx`.

`skeletons/thps5_human.ske.xbx` (Gfx/Skeleton.cpp): `i32 version (2), u32 flags,
i32 n, u32 bone names[n], u32 parent names[n], u32 flip names[n]`, then per bone a
quaternion (xyzw) and a translation (xyzw) relative to the parent. Quat→matrix is
`QuatVecToMatrix` (inverts the quaternion first; row vectors, world = local × parent).
Bone names resolve through the name tables in `qb.pre`. Pro skaters stand in a T-pose,
1.83 m tall, facing +Z in THUG space. A pro's look (`appearance_<name>` in
`scripts/game/cas_skater_m.qb`) is the body skin alone, head included;
`head_<name>.skin` is the matching created-skater head and must not be added on top.
The importer re-aims only bones with a child, so the exporter re-poses the skater to
the stock rig's A-pose first (head and hands keep their parent's rotation).
