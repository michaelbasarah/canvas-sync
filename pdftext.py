"""Minimal PDF text extractor with ToUnicode CMap support (no external deps)."""
import re, zlib, sys, os

def objects(data):
    out = {}
    for m in re.finditer(rb'(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj', data, re.S):
        out[int(m.group(1))] = m.group(3)
    return out

def stream_of(body):
    m = re.search(rb'stream\r?\n', body)
    if not m: return None
    e = body.rfind(b'endstream')
    raw = body[m.end():e].rstrip(b'\r\n') if e > 0 else body[m.end():]
    if b'FlateDecode' in body[:m.start()]:
        try: return zlib.decompressobj().decompress(raw)
        except Exception: return None
    return raw

def parse_cmap(s):
    """Return (mapping, code_byte_len)."""
    mp, blen = {}, 1
    for m in re.finditer(rb'beginbfchar(.*?)endbfchar', s, re.S):
        for src, dst in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', m.group(1)):
            blen = max(blen, len(src) // 2)
            u = ''.join(chr(int(dst[i:i+4], 16)) for i in range(0, len(dst), 4))
            mp[int(src, 16)] = u
    for m in re.finditer(rb'beginbfrange(.*?)endbfrange', s, re.S):
        body = m.group(1)
        for lo, hi, dst in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', body):
            blen = max(blen, len(lo) // 2)
            a, b, d = int(lo, 16), int(hi, 16), int(dst, 16)
            for i in range(min(b - a + 1, 65536)): mp[a + i] = chr(d + i)
        for lo, hi, arr in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]', body, re.S):
            blen = max(blen, len(lo) // 2)
            a = int(lo, 16)
            for i, dst in enumerate(re.findall(rb'<([0-9A-Fa-f]+)>', arr)):
                mp[a + i] = ''.join(chr(int(dst[j:j+4], 16)) for j in range(0, len(dst), 4))
    return mp, blen

def build_fonts(data, objs):
    """Map resource name (/F1) -> (cmap, byte_len)."""
    cmaps = {}
    for num, body in objs.items():
        if b'/Type' in body and b'/Font' in body:
            tu = re.search(rb'/ToUnicode\s+(\d+)\s+\d+\s+R', body)
            if tu:
                s = stream_of(objs.get(int(tu.group(1)), b''))
                if s: cmaps[num] = parse_cmap(s)
    names = {}
    for body in list(objs.values()) + [data]:
        for m in re.finditer(rb'/Font\s*<<(.*?)>>', body, re.S):
            for name, ref in re.findall(rb'/([A-Za-z0-9#_.+-]+)\s+(\d+)\s+\d+\s+R', m.group(1)):
                if int(ref) in cmaps: names[name.decode('latin-1')] = cmaps[int(ref)]
    return names

def unescape(b):
    out, i = bytearray(), 0
    while i < len(b):
        c = b[i]
        if c == 0x5c and i + 1 < len(b):
            n = b[i+1]
            mp = {ord('n'):10, ord('r'):13, ord('t'):9, ord('b'):8, ord('f'):12}
            if n in mp: out.append(mp[n]); i += 2; continue
            if 0x30 <= n <= 0x37:
                o = re.match(rb'[0-7]{1,3}', b[i+1:i+4]).group()
                out.append(int(o, 8) & 0xFF); i += 1 + len(o); continue
            out.append(n); i += 2; continue
        out.append(c); i += 1
    return bytes(out)

def decode(raw, font):
    if not font: return raw.decode('latin-1')
    mp, blen = font
    if blen == 1:
        return ''.join(mp.get(c, chr(c)) for c in raw)
    out = []
    for i in range(0, len(raw) - 1, 2):
        code = (raw[i] << 8) | raw[i+1]
        out.append(mp.get(code, ''))
    return ''.join(out)

TOKEN = re.compile(rb'/([A-Za-z0-9#_.+-]+)\s+[\d.]+\s+Tf'
                   rb'|\[((?:[^\[\]\\]|\\.)*)\]\s*TJ'
                   rb'|\(((?:[^()\\]|\\.)*)\)\s*Tj'
                   rb'|(T\*|\bTd\b|\bTD\b|\bET\b)', re.S)

def text_from(stream, fonts):
    parts, cur = [], None
    for m in TOKEN.finditer(stream):
        if m.group(1) is not None:
            cur = fonts.get(m.group(1).decode('latin-1'))
        elif m.group(2) is not None:
            inner = m.group(2)
            seg = ''.join(decode(unescape(s), cur)
                          for s in re.findall(rb'\(((?:[^()\\]|\\.)*)\)', inner, re.S))
            parts.append(seg)
            if any(int(g) < -180 for g in re.findall(rb'\)\s*(-?\d+)', inner)): parts.append(' ')
        elif m.group(3) is not None:
            parts.append(decode(unescape(m.group(3)), cur))
        else:
            parts.append('\n')
    return ''.join(parts)

def extract(path):
    data = open(path, 'rb').read()
    objs = objects(data)
    fonts = build_fonts(data, objs)
    out = []
    for body in objs.values():
        s = stream_of(body)
        if s and (b'Tj' in s or b'TJ' in s):
            out.append(text_from(s, fonts))
    t = '\n'.join(out)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n[ ]*\n[ ]*\n+', '\n\n', t)
    return t.strip()

if __name__ == '__main__':
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
    print(extract(sys.argv[1])[:n])
