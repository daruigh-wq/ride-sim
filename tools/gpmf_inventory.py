#!/usr/bin/env python3
"""Zero-dependency GPMF inventory. Walks the KLV tree in a raw `gpmd` stream and
reports every telemetry stream: name, FourCC, type, sample count, scale, units,
and a decoded first-sample. GPMF = big-endian KLV: 4B FourCC key, 1B type,
1B struct-size, 2B repeat; payload = size*repeat bytes padded to 4."""
import struct, sys
from collections import OrderedDict

TYPE_SIZE = {'b':1,'B':1,'c':1,'d':8,'f':4,'F':4,'G':16,'j':8,'J':8,
             'l':4,'L':4,'q':4,'Q':8,'s':2,'S':2,'U':1}
UNPACK = {'b':'b','B':'B','s':'h','S':'H','l':'i','L':'I','f':'f','d':'d','j':'q','J':'Q'}

def decode(payload, typ, esize, n):
    """Decode payload into a flat list of numbers (best-effort)."""
    if typ in UNPACK:
        fmt = '>' + UNPACK[typ] * (len(payload)//struct.calcsize('>'+UNPACK[typ]))
        try: return list(struct.unpack(fmt, payload[:struct.calcsize(fmt)]))
        except: return []
    if typ == 'q':  # Q15.16 fixed
        vals = struct.unpack('>%di' % (len(payload)//4), payload[:4*(len(payload)//4)])
        return [v/65536.0 for v in vals]
    if typ == 'c' or typ == 'U':
        return [payload.rstrip(b'\x00 ').decode('latin1', 'replace')]
    return []

def decode_complex(payload, typefmt, ssize):
    """Decode a '?' complex stream (e.g. GPS9 'lllllllSS') into a flat list of
    len(typefmt)*repeat numbers, per the sibling TYPE format string."""
    per = [(UNPACK[c], struct.calcsize('>'+UNPACK[c])) for c in typefmt if c in UNPACK]
    if len(per) != len(typefmt) or not ssize:
        return []
    out = []
    for base in range(0, len(payload) - ssize + 1, ssize):
        off = base
        for u, sz in per:
            out.append(struct.unpack('>'+u, payload[off:off+sz])[0]); off += sz
    return out

def walk(buf, off, end, depth, ctx, out):
    while off + 8 <= end:
        key = buf[off:off+4].decode('latin1')
        typ = chr(buf[off+4]); ssize = buf[off+5]; repeat = struct.unpack('>H', buf[off+6:off+8])[0]
        off += 8
        paylen = ssize * repeat
        payload = buf[off:off+paylen]
        off += paylen + ((4 - paylen % 4) % 4)  # 4-byte align
        if typ == '\x00':  # nested container (DEVC / STRM)
            newctx = dict(ctx)
            if key == 'STRM': newctx = {}   # fresh scope per stream
            walk(payload, 0, len(payload), depth+1, newctx, out)
            if key == 'STRM' and newctx.get('_data'):
                out.append(newctx)
            continue
        # leaf: stash modifiers / data in current stream scope
        if key == 'STNM': ctx['STNM'] = decode(payload, 'c', 1, repeat)[0] if payload else ''
        elif key == 'SIUN' or key == 'UNIT': ctx['UNIT'] = decode(payload, 'c', 1, repeat)[0] if payload else ''
        elif key == 'TYPE': ctx['TYPE'] = payload.rstrip(b'\x00 ').decode('latin1','replace')
        elif key == 'SCAL':
            ctx['SCAL'] = decode(payload, typ, TYPE_SIZE.get(typ,1), repeat)
        elif key == 'TSMP': ctx['TSMP'] = decode(payload, typ, 4, repeat)
        elif key == 'DVNM': out_dvnm(ctx, payload)
        elif key not in ('TICK','TOCK','TIMO','EMPT','TSMP','STMP','TYPE','ORIN','ORIO',
                         'MTRX','FMWR','DVID','VERS','GPSU','GPSF','GPSP','GPSA','STPS','KLV5'):
            # treat as the stream's data key if it carries numeric samples.
            # '?' = complex type (e.g. GPS9), decoded via the sibling TYPE string.
            if (typ in TYPE_SIZE or typ == '?') and typ != 'c':
                cur = ctx.get('_data')            # data leaf = the one with the MOST
                if cur is None or repeat > cur[3]:  # samples (beats embedded TMPC etc.)
                    ctx['_data'] = (key, typ, ssize, repeat, payload)
    return out

def out_dvnm(ctx, payload):
    ctx['_DVNM'] = payload.rstrip(b'\x00 ').decode('latin1','replace')

def main(path, duration):
    buf = open(path,'rb').read()
    streams = walk(buf, 0, len(buf), 0, {}, [])
    # aggregate by data FourCC across all DEVC packets
    agg = OrderedDict()
    for s in streams:
        key, typ, ssize, repeat, payload = s['_data']
        a = agg.setdefault(key, {'typ':typ,'ssize':ssize,'samples':0,'STNM':s.get('STNM',''),
                                 'UNIT':s.get('UNIT',''),'SCAL':s.get('SCAL'),'TYPE':s.get('TYPE'),'first':None})
        a['samples'] += repeat
        if a['first'] is None and payload:
            if typ == '?' and s.get('TYPE'):
                vals = decode_complex(payload[:ssize], s['TYPE'], ssize)
                axes = len(vals)
            else:
                esize = TYPE_SIZE.get(typ,1); axes = max(1, ssize//esize)
                vals = decode(payload[:ssize], typ, esize, axes)
            a['first'] = vals
            a['axes'] = axes
    print(f"\n=== GPMF inventory: {path}  (clip {duration:.1f}s) ===")
    print(f"{'FourCC':6} {'name':26} {'typ':3} {'axes':4} {'samples':>8} {'~Hz':>6}  {'scale':>14}  first-sample(scaled)")
    print('-'*118)
    for k,a in agg.items():
        hz = a['samples']/duration if duration else 0
        scal = a.get('SCAL'); axes = a.get('axes',1)
        first = a.get('first') or []
        if scal and first:
            sc = scal*axes if len(scal)==1 else scal
            scaled = [round(first[i]/(sc[i] if i<len(sc) else sc[0]),4) for i in range(len(first))]
        else:
            scaled = first
        scal_s = ('/%s'%scal[0]) if scal and len(scal)==1 else (str(scal) if scal else '-')
        print(f"{k:6} {a['STNM'][:26]:26} {a['typ']:3} {axes:<4} {a['samples']:>8} {hz:>6.1f}  {scal_s:>14}  {scaled} {a['UNIT']}")

if __name__ == '__main__':
    main(sys.argv[1], float(sys.argv[2]))
