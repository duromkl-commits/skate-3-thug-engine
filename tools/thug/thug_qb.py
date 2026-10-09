"""Read data (not script code) from THUG compiled `.qb` files: global structs, arrays and
values such as a level's NodeArray. Token layout from the THUG source
(Gel/Scripting/tokens.h, skiptoken.cpp). Names are Neversoft CRCs; see `crc`.
"""
import struct
import zlib

EOF_, EOL, EOL_NUM, START_STRUCT, END_STRUCT, START_ARRAY, END_ARRAY, EQUALS = range(8)
COMMA, MINUS = 9, 10
NAME, INTEGER, HEXINTEGER, FLOAT, STRING, LOCALSTRING, VECTOR, PAIR = 22, 23, 24, 26, 27, 28, 30, 31
SCRIPT, ENDSCRIPT, CHECKSUM_NAME = 35, 36, 43
RANDOMS = {47, 55, 64, 65}
FIVE_BYTE = {NAME, INTEGER, HEXINTEGER, FLOAT, EOL_NUM, 46, 67, 68}


def crc(name: str) -> int:
    """Neversoft Crc::GenerateCRCFromString (lower case, '/' as '\\', no final xor)."""
    data = name.lower().replace("/", "\\").encode("latin-1")
    return (zlib.crc32(data) ^ 0xFFFFFFFF) & 0xFFFFFFFF


class Name(int):
    """A checksum value (an unquoted name in the source)."""


class Tokens:
    def __init__(self, data: bytes):
        self.d, self.o = data, 0
        self.names: dict[int, str] = {}

    def peek(self) -> int:
        return self.d[self.o] if self.o < len(self.d) else EOF_

    def next(self):
        t = self.d[self.o]
        self.o += 1
        if t in FIVE_BYTE:
            raw = self.d[self.o:self.o + 4]
            self.o += 4
            if t == FLOAT:
                return t, struct.unpack("<f", raw)[0]
            if t in (INTEGER,):
                return t, struct.unpack("<i", raw)[0]
            return t, struct.unpack("<I", raw)[0]
        if t == VECTOR:
            v = struct.unpack_from("<3f", self.d, self.o)
            self.o += 12
            return t, v
        if t == PAIR:
            v = struct.unpack_from("<2f", self.d, self.o)
            self.o += 8
            return t, v
        if t in (STRING, LOCALSTRING):
            n = struct.unpack_from("<I", self.d, self.o)[0]
            s = self.d[self.o + 4:self.o + 4 + n].split(b"\0", 1)[0].decode("latin-1")
            self.o += 4 + n
            return t, s
        if t == CHECKSUM_NAME:
            cs = struct.unpack_from("<I", self.d, self.o)[0]
            end = self.d.index(b"\0", self.o + 4)
            self.names[cs] = self.d[self.o + 4:end].decode("latin-1")
            self.o = end + 1
            return t, cs
        if t in RANDOMS:
            n = struct.unpack_from("<I", self.d, self.o)[0]
            self.o += 4 + 6 * n
            return t, None
        return t, None

    def skip_blank(self):
        while self.peek() in (EOL, EOL_NUM, COMMA):
            self.next()


def _value(tk: Tokens):
    tk.skip_blank()
    t, v = tk.next()
    if t == MINUS:
        t, v = tk.next()
        v = -v
    if t == START_STRUCT:
        return _struct(tk)
    if t == START_ARRAY:
        items = []
        while True:
            tk.skip_blank()
            if tk.peek() == END_ARRAY:
                tk.next()
                return items
            items.append(_value(tk))
    if t == NAME:
        return Name(v)
    return v


def _struct(tk: Tokens) -> dict:
    """Struct as {name checksum: value}; bare names (flags) map to True."""
    out = {}
    while True:
        tk.skip_blank()
        if tk.peek() == END_STRUCT:
            tk.next()
            return out
        t, v = tk.next()
        if t == NAME and tk.peek() == EQUALS:
            tk.next()
            out[v] = _value(tk)
        elif t == NAME:
            out[v] = True
        # anything else (e.g. stray values) is ignored


def read_qb(data: bytes):
    """Return ({global name checksum: value}, {checksum: string} from the file's name table)."""
    tk = Tokens(data)
    globals_ = {}
    while tk.o < len(tk.d):
        tk.skip_blank()
        t, v = tk.next()
        if t == EOF_:
            break
        if t == SCRIPT:
            while tk.o < len(tk.d) and tk.peek() != ENDSCRIPT:
                tk.next()
            continue
        if t == NAME and tk.peek() == EQUALS:
            tk.next()
            globals_[v] = _value(tk)
    while tk.o < len(tk.d):  # trailing CHECKSUM_NAME table
        tk.next()
    return globals_, tk.names


def expand_nodes(nodes: list, globals_: dict) -> list:
    """Merge compressed components: a bare flag naming a global struct (ncomp_*) pulls its fields in."""
    out = []
    for node in nodes:
        merged = {}
        for key, value in node.items():
            shared = globals_.get(key) if value is True else None
            if isinstance(shared, dict):
                merged.update(shared)
        merged.update({k: v for k, v in node.items() if not (v is True and isinstance(globals_.get(k), dict))})
        out.append(merged)
    return out
