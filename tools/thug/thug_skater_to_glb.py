"""Export a THUG PC skater (body + head skins) as a Mixamo-named, skinned GLB that the
engine's Custom Models importer (tools/mixamo_to_skate) can fit to the Skate 3 rig.

usage: python thug_skater_to_glb.py <THUG Game folder> <skater, e.g. campbell> <out.glb>

Reads Data/pre/skaterparts.pre (models/skater_male/skater_<name> and head_<name>) and
Data/pre/skeletons.pre (thps5_human). THUG bones are renamed to Mixamo names; helper
bones (fingers, wrist, cloth, jaw...) fold into the nearest mapped bone. Only the bind
pose is exported, no animation. See docs/thug/FORMATS.md.
"""
import io
import json
import pathlib
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import thug_level  # noqa: E402
from thug_pre import read_pre  # noqa: E402
from thug_qb import crc  # noqa: E402
from thug_to_skate import INCH, decode_texture, strip_triangles, texture_has_alpha  # noqa: E402

# Mixamo name -> THUG bone. Order is parent-before-child.
MIXAMO = [
    ("Hips", "Bone_Pelvis", None),
    ("Spine", "Bone_Stomach_Lower", "Hips"),
    ("Spine1", "Bone_Stomach_Upper", "Spine"),
    ("Spine2", "Bone_Chest", "Spine1"),
    ("Neck", "Bone_Neck", "Spine2"),
    ("Head", "Bone_Head", "Neck"),
]
# THUG data is left-handed. Negating Z (as the level converter does) shows the skater as
# THUG drew him, but mirrored coordinates put THUG's "_L" bones on his anatomical right,
# so the sides swap here. The importer derives "forward" from left/right and the toes.
for side, s in (("Left", "R"), ("Right", "L")):
    MIXAMO += [
        (f"{side}Shoulder", f"Bone_Collar_{s}", "Spine2"),
        (f"{side}Arm", f"Bone_Bicep_{s}", f"{side}Shoulder"),
        (f"{side}ForeArm", f"Bone_Forearm_{s}", f"{side}Arm"),
        (f"{side}Hand", f"Bone_Palm_{s}", f"{side}ForeArm"),
        (f"{side}UpLeg", f"Bone_Thigh_{s}", "Hips"),
        (f"{side}Leg", f"Bone_Knee_{s}", f"{side}UpLeg"),
        (f"{side}Foot", f"Bone_Ankle_{s}", f"{side}Leg"),
        (f"{side}ToeBase", f"Bone_Toe_{s}", f"{side}Foot"),
    ]
# Helper bones whose nearest mapped ancestor is the wrong place for their weights.
FOLD = {f"Bone_Shoulder_{s}": f"Bone_Bicep_{s}" for s in "LR"}  # deltoid moves with the arm


def read_skeleton(data: bytes):
    """thps5_human.ske.xbx -> (bone checksums, parent checksums, model-space bind positions)."""
    _version, _flags, n = struct.unpack_from("<IIi", data)
    names = struct.unpack_from(f"<{n}I", data, 12)
    parents = struct.unpack_from(f"<{n}I", data, 12 + 4 * n)
    off = 12 + 12 * n
    world = []
    for i in range(n):
        qx, qy, qz, qw, tx, ty, tz, _ = struct.unpack_from("<8f", data, off)
        off += 32
        x, y, z, w = -qx, -qy, -qz, qw  # QuatVecToMatrix inverts first
        m = np.eye(4)
        m[0, :3] = (1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (z * x - w * y))
        m[1, :3] = (2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x))
        m[2, :3] = (2 * (z * x + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y))
        m[3, :3] = (tx, ty, tz)
        if i:
            m = m @ world[names.index(parents[i])]  # row vectors: local * parent
        world.append(m)
    return list(names), list(parents), np.array([m[3, :3] for m in world])


def to_glb_space(p):
    p = np.asarray(p, np.float64) * INCH
    return p * np.array([1, 1, -1])  # THUG is left-handed


class Glb:
    def __init__(self):
        self.bin = bytearray()
        self.doc = {"asset": {"version": "2.0", "generator": "thug_skater_to_glb"},
                    "buffers": [{}], "bufferViews": [], "accessors": [], "images": [],
                    "textures": [], "materials": [], "meshes": [], "nodes": [], "skins": [],
                    "samplers": [{"magFilter": 9729, "minFilter": 9987}], "scenes": [{"nodes": []}],
                    "scene": 0}

    def view(self, data: bytes, target=None):
        while len(self.bin) % 4:
            self.bin += b"\0"
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.doc["bufferViews"].append(v)
        return len(self.doc["bufferViews"]) - 1

    def accessor(self, array, kind, component, target=None, bounds=False):
        dtype = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[component]
        array = np.ascontiguousarray(array, dtype)
        a = {"bufferView": self.view(array.tobytes(), target), "componentType": component,
             "count": len(array), "type": kind}
        if bounds:
            a["min"], a["max"] = array.min(0).tolist(), array.max(0).tolist()
        self.doc["accessors"].append(a)
        return len(self.doc["accessors"]) - 1

    def save(self, path):
        while len(self.bin) % 4:
            self.bin += b"\0"
        self.doc["buffers"][0]["byteLength"] = len(self.bin)
        js = json.dumps({k: v for k, v in self.doc.items() if v != []}, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin))
        out += struct.pack("<II", len(js), 0x4E4F534A) + js
        out += struct.pack("<II", len(self.bin), 0x004E4942) + bytes(self.bin)
        pathlib.Path(path).write_bytes(out)


def convert(game_dir: pathlib.Path, skater: str, out_path: pathlib.Path):
    pre = game_dir / "Data" / "pre"
    parts = read_pre(pre / "skaterparts.pre")
    names, parents, positions = read_skeleton(read_pre(pre / "skeletons.pre")["skeletons/thps5_human.ske.xbx"])
    by_crc = {c: i for i, c in enumerate(names)}

    mapped = {crc(thug): mixamo for mixamo, thug, _ in MIXAMO}
    fold = {crc(k): crc(v) for k, v in FOLD.items()}

    def target(bone_index):
        c = names[bone_index]
        c = fold.get(c, c)
        while c not in mapped:
            c = parents[by_crc[c]]
        return mapped[c]

    g = Glb()
    joint_index = {}
    world = {}
    for mixamo, thug, parent in MIXAMO:
        pos = to_glb_space(positions[by_crc[crc(thug)]])
        world[mixamo] = pos
        local = pos - world[parent] if parent else pos
        g.doc["nodes"].append({"name": f"mixamorig:{mixamo}", "translation": local.tolist()})
        joint_index[mixamo] = len(g.doc["nodes"]) - 1
        if parent:
            g.doc["nodes"][joint_index[parent]].setdefault("children", []).append(joint_index[mixamo])
    joints = [joint_index[m] for m, _, _ in MIXAMO]
    order = {m: k for k, (m, _, _) in enumerate(MIXAMO)}
    ibm = np.stack([np.eye(4) for _ in MIXAMO])
    for k, (m, _, _) in enumerate(MIXAMO):
        ibm[k, :3, 3] = -world[m]
    g.doc["skins"].append({"joints": joints, "skeleton": joint_index["Hips"],
                           "inverseBindMatrices": g.accessor(ibm.transpose(0, 2, 1).reshape(-1, 16), "MAT4", 5126)})

    primitives, texture_slot = [], {}
    report = {"vertices": 0, "triangles": 0}
    for part in (f"models/skater_male/skater_{skater}", f"models/skater_male/head_{skater}"):
        if f"{part}.skin.xbx" not in parts:
            sys.exit(f"{part}.skin.xbx not found in skaterparts.pre")
        materials, sectors = thug_level.read_scn(parts[f"{part}.skin.xbx"])
        textures = {t.checksum: t for t in thug_level.read_tex(parts[f"{part}.tex.xbx"])}
        mat_tex = {m.checksum: (m.passes[0].texture if m.passes else 0) for m in materials}
        for sector in sectors:
            if not sector.weights:
                continue
            pos = to_glb_space(sector.positions)
            nrm = np.asarray(sector.normals or [(0, 1, 0)] * len(pos), np.float64) * np.array([1, 1, -1])
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
            uv = np.asarray([u[0] for u in sector.uvs], np.float64) if sector.uvs else np.zeros((len(pos), 2))
            jw = np.zeros((len(pos), 4), np.uint16)
            ww = np.zeros((len(pos), 4), np.float64)
            for v, weights in enumerate(sector.weights):
                acc = {}
                for bone, w in weights:
                    t = target(bone)
                    acc[t] = acc.get(t, 0.0) + w
                for k, (t, w) in enumerate(sorted(acc.items(), key=lambda x: -x[1])[:4]):
                    jw[v, k], ww[v, k] = order[t], w
            ww /= np.maximum(ww.sum(1, keepdims=True), 1e-9)
            for mesh in sector.meshes:
                tris = [t for t in strip_triangles(mesh.indices) if max(t) < len(pos)]
                if not tris:
                    continue
                used = sorted({i for t in tris for i in t})
                remap = {i: k for k, i in enumerate(used)}
                idx = np.array([[remap[t[0]], remap[t[2]], remap[t[1]]] for t in tris], np.uint32)  # Z flip
                tex = mat_tex.get(mesh.material, 0)
                if tex not in texture_slot and tex in textures:
                    rgba = decode_texture(textures[tex])
                    t = textures[tex]
                    buf = io.BytesIO()
                    Image.frombytes("RGBA", (t.width, t.height), rgba).save(buf, "PNG")
                    g.doc["images"].append({"bufferView": g.view(buf.getvalue()), "mimeType": "image/png"})
                    g.doc["textures"].append({"source": len(g.doc["images"]) - 1, "sampler": 0})
                    material = {"name": f"thug_{tex:08x}", "doubleSided": False,
                                "pbrMetallicRoughness": {"baseColorTexture": {"index": len(g.doc["textures"]) - 1},
                                                         "metallicFactor": 0.0, "roughnessFactor": 0.9}}
                    if texture_has_alpha(rgba):
                        material.update(alphaMode="MASK", alphaCutoff=0.5)
                    g.doc["materials"].append(material)
                    texture_slot[tex] = len(g.doc["materials"]) - 1
                prim = {"attributes": {
                    "POSITION": g.accessor(pos[used], "VEC3", 5126, 34962, bounds=True),
                    "NORMAL": g.accessor(nrm[used], "VEC3", 5126, 34962),
                    "TEXCOORD_0": g.accessor(uv[used], "VEC2", 5126, 34962),
                    "JOINTS_0": g.accessor(jw[used], "VEC4", 5123, 34962),
                    "WEIGHTS_0": g.accessor(ww[used], "VEC4", 5126, 34962)},
                    "indices": g.accessor(idx.reshape(-1), "SCALAR", 5125, 34963), "mode": 4}
                if tex in texture_slot:
                    prim["material"] = texture_slot[tex]
                primitives.append(prim)
                report["vertices"] += len(used)
                report["triangles"] += len(idx)
    g.doc["meshes"].append({"name": f"thug_{skater}", "primitives": primitives})
    g.doc["nodes"].append({"name": f"thug_{skater}", "mesh": 0, "skin": 0})
    g.doc["scenes"][0]["nodes"] = [joint_index["Hips"], len(g.doc["nodes"]) - 1]
    g.save(out_path)
    return report


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    print(convert(pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])))
