"""Parse THUG level files (`.scn.xbx`, `.col.xbx`, `.tex.xbx`) as shipped in the PC port.

Layouts follow the THUG source's Xbox loaders; see docs/thug/FORMATS.md. Units are inches.
"""
import struct
from dataclasses import dataclass, field


class Reader:
    def __init__(self, data: bytes, off: int = 0):
        self.d, self.o = data, off

    def take(self, fmt: str):
        v = struct.unpack_from("<" + fmt, self.d, self.o)
        self.o += struct.calcsize("<" + fmt)
        return v

    def one(self, fmt: str):
        return self.take(fmt)[0]

    def bytes(self, n: int) -> bytes:
        b = self.d[self.o:self.o + n]
        self.o += n
        return b

    def at_end(self) -> bool:
        return self.o == len(self.d)


# ---- textures -------------------------------------------------------------------------------

@dataclass
class Texture:
    checksum: int
    width: int
    height: int
    texel_depth: int
    dxt: int
    palette: bytes
    mips: list


def read_tex(data: bytes) -> list[Texture]:
    r = Reader(data)
    _version, count = r.take("II")
    out = []
    for _ in range(count):
        cs, w, h, levels, depth, _pal_depth, dxt, pal_size = r.take("8I")
        palette = r.bytes(pal_size)
        mips = [r.bytes(r.one("I")) for _ in range(levels)]
        out.append(Texture(cs, w, h, depth, dxt, palette, mips))
    if not r.at_end():
        raise ValueError(f"tex: {len(data) - r.o} trailing bytes")
    return out


# ---- scene ----------------------------------------------------------------------------------

UV_WIBBLE, VC_WIBBLE, TEXTURE_ANIMATES = 0x1, 0x2, 0x800


@dataclass
class Pass:
    texture: int
    flags: int
    color: tuple
    blend: int
    fixed_alpha: int


@dataclass
class SceneMaterial:
    checksum: int
    name_checksum: int
    alpha_cutoff: int
    single_sided: bool
    passes: list


@dataclass
class Mesh:
    material: int
    flags: int
    indices: list  # LOD 0 index list (strip or list, see FORMATS.md)


@dataclass
class Sector:
    checksum: int
    flags: int
    positions: list
    normals: list | None
    uvs: list | None  # per vertex, list of (u, v) per texture set
    colors: list | None  # ARGB u32, baked lighting
    meshes: list = field(default_factory=list)


def _read_material(r: Reader) -> SceneMaterial:
    cs, name_cs, passes, alpha_cutoff = r.take("4I")
    _sorted, _draw_order, single_sided, _no_bfc, _zbias, grassify = r.take("?f??i?")
    if grassify:
        r.take("fi")
    if r.one("f") > 0.0:
        r.take("3f")
    out = []
    for p in range(passes):
        tex, flags = r.take("II")
        _has_color = r.one("?")
        color = r.take("3f")
        reg_alpha = r.one("Q")
        r.take("II2fI")  # u/v addressing, envmap tiling, filtering
        if flags & UV_WIBBLE:
            r.take("8f")
        if p == 0 and flags & VC_WIBBLE:
            for _ in range(r.one("I")):
                keys, _phase = r.take("Ii")
                r.bytes(keys * 8)
        if flags & TEXTURE_ANIMATES:
            keys, _period, _iterations, _phase = r.take("4i")
            frames = [r.take("II") for _ in range(keys)]
            tex = frames[0][1] if frames else tex
        r.take("4I")  # mip filtering / LOD bias, always present
        out.append(Pass(tex, flags, color, reg_alpha & 0xFFFFFF, reg_alpha >> 32))
    return SceneMaterial(cs, name_cs, alpha_cutoff, single_sided, out)


def _read_sector(r: Reader) -> Sector:
    cs, _bone, flags, num_mesh = r.take("IiiI")
    r.take("6f4f")  # bbox, bounding sphere
    if flags & 0x800000:
        r.take("I9f")  # billboard
    n, _stride = r.take("ii")
    pos = [r.take("3f") for _ in range(n)]
    normals = [r.take("3f") for _ in range(n)] if flags & 0x04 else None
    if flags & 0x10:
        r.bytes(n * 4 + n * 8)  # weights, bone indices
    uvs = None
    if flags & 0x01:
        sets = r.one("i")
        if sets > 0:
            uvs = [[r.take("2f") for _ in range(sets)] for _ in range(n)]
    colors = list(r.take(f"{n}I")) if flags & 0x02 else None
    if flags & 0x800:
        r.bytes(n)
    sector = Sector(cs, flags, pos, normals, uvs, colors)
    for _ in range(num_mesh):
        r.take("3ff3f3f")  # centre, radius, bbox
        mflags, material, lods = r.take("III")
        lod_indices = []
        for _ in range(lods):
            count = r.one("i")
            lod_indices.append(list(r.take(f"{count}H")))
        sector.meshes.append(Mesh(material, mflags, lod_indices[0] if lod_indices else []))
    return sector


def read_scn(data: bytes):
    r = Reader(data)
    r.take("3I")  # material, mesh, vertex versions
    materials = [_read_material(r) for _ in range(r.one("I"))]
    sectors = [_read_sector(r) for _ in range(r.one("i"))]
    hierarchy = r.one("i")
    r.o = len(data) if hierarchy > 0 else r.o  # hierarchy objects are not needed
    if not r.at_end():
        raise ValueError(f"scn: {len(data) - r.o} trailing bytes")
    return materials, sectors


# ---- collision ------------------------------------------------------------------------------

@dataclass
class CollFace:
    flags: int
    terrain: int
    verts: tuple  # three (x, y, z) in inches


@dataclass
class CollObject:
    checksum: int
    flags: int
    faces: list


def read_col(data: bytes) -> list[CollObject]:
    version, num_obj, total_verts, faces_large, faces_small, verts_large, verts_small, _ = \
        struct.unpack_from("<8i", data, 0)
    if version != 9:
        raise ValueError(f"col: version {version}, expected 9")
    objs_off = 32
    vert_base = (objs_off + num_obj * 64 + 15) & ~15
    intensity = vert_base + verts_large * 12 + verts_small * 6
    face_base = (intensity + total_verts + 3) & ~3
    out = []
    for i in range(num_obj):
        o = objs_off + i * 64
        cs, flags, nv, nf, small, fixed, face_off = struct.unpack_from("<IHHHBBI", data, o)
        bmin = struct.unpack_from("<3f", data, o + 16)
        vert_off = struct.unpack_from("<I", data, o + 48)[0]
        verts = []
        for v in range(nv):
            if fixed:
                q = struct.unpack_from("<3H", data, vert_base + vert_off + v * 6)
                verts.append(tuple(bmin[k] + q[k] / 16.0 for k in range(3)))
            else:
                verts.append(struct.unpack_from("<3f", data, vert_base + vert_off + v * 12))
        faces = []
        for f in range(nf):
            if small:
                fl, terr, a, b, c = struct.unpack_from("<HHBBB", data, face_base + face_off + f * 8)
            else:
                fl, terr, a, b, c = struct.unpack_from("<HHHHH", data, face_base + face_off + f * 10)
            faces.append(CollFace(fl, terr, (verts[a], verts[b], verts[c])))
        out.append(CollObject(cs, flags, faces))
    return out
