"""Convert a Tony Hawk's Underground PC level into a `.skate` map (SKATE09).

usage: python thug_to_skate.py <THUG Game folder> <level, e.g. NJ> <out.skate>

Reads Data/pre/<level>scn.pre and <level>col.pre from your own THUG PC install.
and <level>.pre (the level's node array). What carries over: render geometry (pass 0
texture), textures, collision with THUG terrain types mapped to Skate 3 audio surfaces,
rails (RailNode chains) and the Player1 restart as the spawn. Not yet: ladders, ledges,
baked vertex lighting, multi-pass blending. See docs/thug/FORMATS.md.
"""
import math
import pathlib
import struct
import sys
import zlib

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import thug_level  # noqa: E402
from thug_pre import read_pre  # noqa: E402
from thug_qb import crc, expand_nodes, read_qb  # noqa: E402

INCH = 0.0254

# THUG terrain (Gfx/nxflags.h ETerrainType) -> Skate 3 7-bit audio tag
# (crates/skate-game/src/game_audio/grain_bed.rs::grain_for). Unlisted -> asphalt_smooth (1).
CONCRETE_SMOOTH, CONCRETE_ROUGH, AGGREGATE, WOOD, METAL, ASPHALT = 3, 4, 5, 6, 9, 1
TERRAIN_AUDIO = {
    0: ASPHALT, 16: ASPHALT,                                         # default, asphalt
    1: CONCRETE_SMOOTH, 19: CONCRETE_SMOOTH, 15: CONCRETE_SMOOTH,    # concsmooth, sidewalk, tile
    37: CONCRETE_SMOOTH, 28: CONCRETE_SMOOTH, 29: CONCRETE_SMOOTH,   # wetconc, plexiglass, fiberglass
    33: CONCRETE_SMOOTH,                                             # metalfuture
    2: CONCRETE_ROUGH, 14: CONCRETE_ROUGH, 17: CONCRETE_ROUGH,       # concrough, brick, rock
    18: AGGREGATE, 23: AGGREGATE,                                    # gravel, dirtpacked
    # grass, grassdried, dirt, sand, snow, ice, carpet: no Skate 3 rolling sound yet
    20: AGGREGATE, 21: AGGREGATE, 22: AGGREGATE, 27: AGGREGATE, 26: AGGREGATE, 25: CONCRETE_SMOOTH,
    30: CONCRETE_SMOOTH,
    **{t: WOOD for t in (8, 9, 10, 11, 12, 13)},                     # wood family
    **{t: METAL for t in (3, 4, 5, 6, 7, 31, 32, 38)},               # metal, conveyor, chainlink, fence
}
TERRAIN_WATER = 24
PHYSICS_GROUND, PHYSICS_WATER = 1, 12

FACE_NON_COLLIDABLE, FACE_TRIGGER = 0x10, 0x40


def surface_tag(terrain: int) -> int:
    physics = PHYSICS_WATER if terrain == TERRAIN_WATER else PHYSICS_GROUND
    return (physics << 7) | TERRAIN_AUDIO.get(terrain, ASPHALT)


def to_skate(p):
    # THUG is left-handed (Z flipped against Bevy), in inches.
    return (p[0] * INCH, p[1] * INCH, -p[2] * INCH)


# ---- textures -------------------------------------------------------------------------------

def _rgb565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], -1).astype(np.int32)


def _dxt_colors(blocks, dxt1):
    c0 = blocks[:, 0].astype(np.uint32) | (blocks[:, 1].astype(np.uint32) << 8)
    c1 = blocks[:, 2].astype(np.uint32) | (blocks[:, 3].astype(np.uint32) << 8)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    four = (c0 > c1) | (not dxt1)
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)                                  # (n, 4, 3)
    alpha = np.full((len(blocks), 4), 255, np.int32)
    if dxt1:
        alpha[:, 3] = np.where(four, 255, 0)
    bits = blocks[:, 4:8].astype(np.uint32)
    bits = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3   # (n, 16)
    rgb = np.take_along_axis(pal, idx[:, :, None].astype(np.int64).repeat(3, 2), 1)
    a = np.take_along_axis(alpha, idx.astype(np.int64), 1)
    return rgb, a


def decode_texture(tex: thug_level.Texture) -> bytes:
    w, h, data = tex.width, tex.height, tex.mips[0]
    if tex.dxt:
        bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
        size = 8 if tex.dxt in (1, 2) else 16
        raw = np.frombuffer(data[:bw * bh * size], np.uint8).reshape(-1, size)
        if size == 8:
            rgb, a = _dxt_colors(raw, True)
        else:
            rgb, _ = _dxt_colors(raw[:, 8:], False)
            a0, a1 = raw[:, 0].astype(np.int32), raw[:, 1].astype(np.int32)
            ab = np.zeros(len(raw), np.uint64)
            for k in range(6):
                ab |= raw[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
            ai = ((ab[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
            eight = (a0 > a1)[:, None]
            k = np.arange(8)
            lerp8 = ((7 - k[2:]) * a0[:, None] + (k[2:] - 1) * a1[:, None]) // 7
            lerp6 = ((5 - k[2:6]) * a0[:, None] + (k[2:6] - 1) * a1[:, None]) // 5
            six = np.concatenate([lerp6, np.zeros((len(raw), 1), np.int32), np.full((len(raw), 1), 255)], 1)
            table = np.concatenate([a0[:, None], a1[:, None], np.where(eight, lerp8, six)], 1)
            a = np.take_along_axis(table, ai, 1)
        px = np.concatenate([rgb, a[:, :, None]], 2).astype(np.uint8)   # (blocks, 16, 4)
        img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
        return img[:h, :w].tobytes()
    if tex.texel_depth == 32:  # A8R8G8B8 (assumed linear on PC; Xbox swizzles)
        bgra = np.frombuffer(data[:w * h * 4], np.uint8).reshape(h, w, 4)
        return bgra[:, :, [2, 1, 0, 3]].tobytes()
    if tex.texel_depth == 8 and tex.palette:
        pal = np.frombuffer(tex.palette, np.uint8).reshape(-1, 4)[:, [2, 1, 0, 3]]
        return pal[np.frombuffer(data[:w * h], np.uint8)].tobytes()
    return bytes([255, 0, 255, 255]) * (w * h)  # unsupported: magenta


def texture_has_alpha(rgba: bytes) -> bool:
    return np.frombuffer(rgba, np.uint8)[3::4].min() < 250


# ---- geometry -------------------------------------------------------------------------------

def strip_triangles(ix):
    """Triangle strip (with degenerate stitches) -> triangle list, winding alternated."""
    for i in range(len(ix) - 2):
        a, b, c = ix[i], ix[i + 1], ix[i + 2]
        if a == b or b == c or a == c:
            continue
        yield (a, b, c) if i % 2 == 0 else (b, a, c)


def cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def finite(values, fallback):
    return values if all(math.isfinite(x) for x in values) else fallback


def normalize(n):
    length = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
    return (n[0] / length, n[1] / length, n[2] / length)


# ---- writer ---------------------------------------------------------------------------------

class Out:
    def __init__(self):
        self.b = bytearray(b"SKATE09\0")

    def u(self, *v):
        self.b += struct.pack(f"<{len(v)}I", *v)

    def f(self, *v):
        self.b += struct.pack(f"<{len(v)}f", *v)

    def s(self, text):
        data = text.encode()
        self.u(len(data))
        self.b += data

    def block(self, payload: bytes):
        packed = zlib.compress(payload, 6)
        self.u(1, len(packed))
        self.b += packed


# Environment block copied from tools/make_skate_demo.py (45 floats): sky/fog/sun defaults.
ENVIRONMENT = [
    .09, .34, .72, .58, .78, .98, .18, .25, .34, 0, 12, .62, 17, 0,
    .045, .10, .26, 1, .32, .10, .05, .035, .06,
    .007, .015, .045, .045, .085, .17, .008, .014, .032,
    1, .92, .78, .42, .56, .92, 1.25, .18, .32, .11, 1, 1, 1,
]


def convert(game_dir: pathlib.Path, level: str, out_path: pathlib.Path):
    pre_dir = game_dir / "Data" / "pre"
    files = read_pre(pre_dir / f"{level}scn.pre")
    files.update(read_pre(pre_dir / f"{level}col.pre"))
    key = level.lower()
    base = f"levels/{key}/{key}"
    textures = thug_level.read_tex(files[f"{base}.tex.xbx"])
    materials, sectors = thug_level.read_scn(files[f"{base}.scn.xbx"])
    collision = thug_level.read_col(files[f"{base}.col.xbx"])

    # Textures (1-based ids in .skate).
    tex_id, tex_rgba, tex_alpha = {}, [], {}
    for t in textures:
        rgba = decode_texture(t)
        tex_id[t.checksum] = len(tex_rgba) + 1
        tex_rgba.append((f"{t.checksum:08x}", t.width, t.height, rgba))
        tex_alpha[t.checksum] = texture_has_alpha(rgba)

    # Materials: one per THUG material, pass 0 only. Id 1 is a fallback for collision.
    mat_id = {}
    mat_rows = [("thug_collision", 0, 0, 0)]
    for m in materials:
        p0 = m.passes[0] if m.passes else None
        texture = tex_id.get(p0.texture, 0) if p0 else 0
        if p0 and p0.blend & 0xFF:
            alpha_mode = 2
        elif p0 and tex_alpha.get(p0.texture):
            alpha_mode = 1
        else:
            alpha_mode = 0
        mat_id[m.checksum] = len(mat_rows) + 1
        mat_rows.append((f"{m.checksum:08x}", texture, alpha_mode, m.alpha_cutoff / 255.0))

    # Render geometry: unshared vertices per mesh so no triangle mixes materials.
    verts, indices = [], []
    for sector in sectors:
        for mesh in sector.meshes:
            mid = mat_id.get(mesh.material, 1)
            remap = {}
            for tri in strip_triangles(mesh.indices):
                pts = []
                for i in tri:
                    if i >= len(sector.positions):
                        break
                    pts.append(i)
                if len(pts) != 3:
                    continue
                p = [to_skate(sector.positions[i]) for i in pts]
                if not all(math.isfinite(x) for q in p for x in q):
                    continue
                if cross(sub(p[1], p[0]), sub(p[2], p[0])) == (0.0, 0.0, 0.0):
                    continue
                for i in reversed(pts):  # Z flip mirrors, so reverse winding
                    if i not in remap:
                        n = finite(sector.normals[i] if sector.normals else (0, 1, 0), (0, 1, 0))
                        uv = finite(sector.uvs[i][0] if sector.uvs else (0, 0), (0, 0))
                        remap[i] = len(verts)
                        verts.append((*to_skate(sector.positions[i]), n[0], n[1], -n[2],
                                      uv[0], uv[1], 0.0, 0.0, mid))
                    indices.append(remap[i])

    # Collision.
    coll = []
    for obj in collision:
        for face in obj.faces:
            if face.flags & (FACE_NON_COLLIDABLE | FACE_TRIGGER):
                continue
            a, b, c = (to_skate(v) for v in face.verts)
            a, b, c = a, c, b  # Z flip mirrors, so reverse winding
            n = cross(sub(b, a), sub(c, a))
            if not all(map(math.isfinite, n)) or (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) <= 1e-12:
                continue
            coll.append((a, b, c, surface_tag(face.terrain)))

    nodes = []
    if (pre_dir / f"{level}.pre").exists():
        level_files = read_pre(pre_dir / f"{level}.pre")
        if f"{base}.qb" in level_files:
            globals_, _ = read_qb(level_files[f"{base}.qb"])
            nodes = expand_nodes(globals_.get(crc("NodeArray"), []), globals_)
    rails = read_rails(nodes)
    spawn, heading = read_spawn(nodes) or (spawn_guess(coll), 0.0)

    out = Out()
    out.u(0x12345678)
    out.s(f"THUG {level}")
    out.f(*spawn, heading)
    out.f(*ENVIRONMENT)
    out.u(len(mat_rows), len(tex_rgba), len(verts), len(indices), len(coll), len(rails), 0, 0, 0)
    for name, texture, alpha_mode, cutoff in mat_rows:
        out.s(name)
        out.u(1)
        out.f(.6, .1, 1, 1, 1, .85, 0)       # friction, restitution, colour, roughness, emissive
        out.u(texture, 0)                     # albedo, indirect
        out.f(1)                              # indirect strength
        out.u(0, 0, 0, alpha_mode)            # normal, ORM, emissive, alpha mode
        out.f(min(max(cutoff, 0.0), 1.0) or .5)
        out.u(ASPHALT, PHYSICS_GROUND, 0)     # render material audio / physics / pattern
    for name, w, h, rgba in tex_rgba:
        out.s(name)
        out.u(w, h, 1)                        # sRGB
        out.block(rgba)
    vbytes = bytearray()
    for v in verts:
        vbytes += struct.pack("<10fI", *v)
    out.block(bytes(vbytes))
    out.block(struct.pack(f"<{len(indices)}I", *indices))
    cbytes = bytearray()
    for a, b, c, tag in coll:
        cbytes += struct.pack("<9fII", *a, *b, *c, tag, 1)
    out.block(bytes(cbytes))
    for name, closed, points in rails:
        out.s(name)
        out.u(int(closed), len(points))
        for p in points:
            out.f(*p)
    out_path.write_bytes(out.b)
    return dict(textures=len(tex_rgba), materials=len(mat_rows), vertices=len(verts),
                triangles=len(indices) // 3, collision=len(coll), rails=len(rails),
                spawn=spawn, heading=heading, bytes=len(out.b))


CLASS, POS, ANGLES, LINKS, NAME, TYPE = (crc(n) for n in ("Class", "Pos", "Angles", "Links", "Name", "Type"))


def read_rails(nodes):
    """RailNode chains (Links point at the next node) -> [(name, closed, points in metres)]."""
    rail = {i for i, n in enumerate(nodes) if n.get(CLASS) == crc("RailNode") and POS in n}
    nxt = {}
    for i in rail:
        links = [j for j in nodes[i].get(LINKS, []) if isinstance(j, int) and j in rail]
        if links:
            nxt[i] = links[0]
    has_prev = set(nxt.values())
    out, seen = [], set()
    starts = [i for i in sorted(rail) if i not in has_prev] + sorted(rail)  # loops have no start
    for start in starts:
        if start in seen:
            continue
        chain, i = [], start
        while i is not None and i not in seen:
            seen.add(i)
            chain.append(i)
            i = nxt.get(i)
        closed = i == start and len(chain) > 2
        if len(chain) >= 2:
            out.append((f"rail_{start}", closed, [to_skate(nodes[j][POS]) for j in chain]))
    return out


def read_spawn(nodes):
    """The Player1 restart (else the first restart): position in metres and heading."""
    restarts = [n for n in nodes if n.get(CLASS) == crc("Restart") and POS in n]
    if not restarts:
        return None
    player1 = [n for n in restarts if n.get(TYPE) == crc("Player1")]
    node = (player1 or restarts)[0]
    yaw = node.get(ANGLES, (0, 0, 0))[1]
    x, y, z = to_skate(node[POS])
    return (x, y + 0.3, z), -yaw  # lift off the floor; Z flip mirrors the yaw


def spawn_guess(coll):
    """Temporary: highest-density upward floor point until THUG restart nodes are read."""
    best = None
    for a, b, c, _ in coll:
        n = normalize(cross(sub(b, a), sub(c, a)))
        if n[1] > 0.95:
            centre = tuple((a[k] + b[k] + c[k]) / 3 for k in range(3))
            area = math.sqrt(sum(x * x for x in cross(sub(b, a), sub(c, a))))
            if best is None or area > best[0]:
                best = (area, centre)
    centre = best[1] if best else (0.0, 0.0, 0.0)
    return (centre[0], centre[1] + 1.0, centre[2])


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    print(convert(pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])))
