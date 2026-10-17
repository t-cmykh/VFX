"""Lecteur protobuf minimal (format filaire), sans protoc ni code genere.

Les numeros de champs sont lus dans rocktree.py (copie du schema : rocktree.proto).
Avantage : aucune dependance a une version precise de la lib protobuf, et un
changement de numero de champ cote Google se corrige en une ligne.
"""
import struct


def read_varint(buf, pos):
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("varint tronque")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


class Message:
    """Message decode paresseusement : champ -> liste de (type filaire, valeur brute)."""

    def __init__(self, buf):
        self.fields = {}
        buf = memoryview(bytes(buf))
        pos, end = 0, len(buf)
        while pos < end:
            key, pos = read_varint(buf, pos)
            num, wt = key >> 3, key & 7
            if wt == 0:
                val, pos = read_varint(buf, pos)
            elif wt == 1:
                val, pos = bytes(buf[pos:pos + 8]), pos + 8
            elif wt == 2:
                n, pos = read_varint(buf, pos)
                val, pos = bytes(buf[pos:pos + n]), pos + n
            elif wt == 5:
                val, pos = bytes(buf[pos:pos + 4]), pos + 4
            else:
                raise ValueError(f"type filaire {wt} non gere (champ {num})")
            if pos > end:
                raise ValueError(f"champ {num} depasse la fin du message")
            self.fields.setdefault(num, []).append((wt, val))

    def has(self, num):
        return num in self.fields

    def uint(self, num, default=None):
        v = self.fields.get(num)
        return v[-1][1] if v else default

    def bytes(self, num, default=b""):
        v = self.fields.get(num)
        return v[-1][1] if v else default

    def all_bytes(self, num):
        return [val for _, val in self.fields.get(num, [])]

    def string(self, num, default=""):
        v = self.fields.get(num)
        return v[-1][1].decode("utf-8") if v else default

    def msg(self, num):
        v = self.fields.get(num)
        return Message(v[-1][1]) if v else None

    def msgs(self, num):
        return [Message(val) for _, val in self.fields.get(num, [])]

    def float(self, num, default=None):
        v = self.fields.get(num)
        return struct.unpack("<f", v[-1][1])[0] if v else default

    def _repeated(self, num, fmt, size):
        out = []
        for wt, val in self.fields.get(num, []):
            if wt == 2:  # packed
                out.extend(struct.unpack(f"<{len(val) // size}{fmt}", val))
            else:
                out.append(struct.unpack(f"<{fmt}", val)[0])
        return out

    def doubles(self, num):
        return self._repeated(num, "d", 8)

    def floats(self, num):
        return self._repeated(num, "f", 4)
