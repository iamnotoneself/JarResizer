# -*- coding: utf-8 -*-
"""Convert ProGuard -microedition StackMap attributes (u2 nloc/nstk)
to BlackBerry CLDC format (u1 nloc/nstk).

ProGuard writes number_of_locals / number_of_stack_items as u2
(StackMapTable FULL_FRAME style); the BlackBerry CLDC verifier expects u1.
The mismatch garbles parsing -> device verification error 1908, surfaced as
NoClassDefFoundError at class load.

Usage: python convert_stackmap.py in.jar out.jar
"""
import zipfile, struct, sys, os


class R:
    def __init__(s, b):
        s.b = b
        s.p = 0

    def u1(s):
        v = s.b[s.p]
        s.p += 1
        return v

    def u2(s):
        v = struct.unpack_from('>H', s.b, s.p)[0]
        s.p += 2
        return v

    def u4(s):
        v = struct.unpack_from('>I', s.b, s.p)[0]
        s.p += 4
        return v

    def take(s, n):
        v = s.b[s.p:s.p + n]
        s.p += n
        return v


class W:
    def __init__(s):
        s.b = bytearray()

    def u1(s, v):
        s.b.append(v & 0xff)

    def u2(s, v):
        s.b += struct.pack('>H', v & 0xffff)

    def u4(s, v):
        s.b += struct.pack('>I', v & 0xffffffff)

    def raw(s, d):
        s.b += d


def parse_cp(r):
    nc = r.u2()
    cp = [None] * nc
    i = 1
    while i < nc:
        t = r.u1()
        if t == 1:
            k = r.u2()
            cp[i] = (t, r.take(k))
        elif t in (3, 4):
            cp[i] = (t, r.u4())
        elif t in (5, 6):
            cp[i] = (t, r.u4() << 32 | r.u4())
            i += 1
        elif t in (7, 8, 16):
            cp[i] = (t, r.u2())
        elif t in (9, 10, 11, 12):
            cp[i] = (t, r.u2(), r.u2())
        elif t == 15:
            cp[i] = (t, r.u1(), r.u2())
        elif t in (17, 18, 19, 20):
            cp[i] = (t, r.u2())
        else:
            raise Exception('cp tag %d at %d' % (t, i))
        i += 1
    return cp


def cp_name(cp, idx):
    e = cp[idx]
    if not e or e[0] != 1:
        return None
    return e[1].decode('utf-8', 'replace')


def write_cp(W, cp):
    W.u2(len(cp))
    i = 1
    while i < len(cp):
        e = cp[i]
        t = e[0]
        W.u1(t)
        if t == 1:
            W.u2(len(e[1]))
            W.raw(e[1])
        elif t in (3, 4):
            W.u4(e[1])
        elif t in (5, 6):
            W.u4((e[1] >> 32) & 0xffffffff)
            W.u4(e[1] & 0xffffffff)
            i += 1
        elif t in (7, 8, 16):
            W.u2(e[1])
        elif t in (9, 10, 11, 12):
            W.u2(e[1])
            W.u2(e[2])
        elif t == 15:
            W.u1(e[1])
            W.u2(e[2])
        elif t in (17, 18, 19, 20):
            W.u2(e[1])
        i += 1


def parse_vti(r):
    tag = r.u1()
    extra = r.u2() if tag in (7, 8) else None
    return (tag, extra)


def write_vti(W, vti):
    W.u1(vti[0])
    if vti[0] in (7, 8):
        W.u2(vti[1])


def convert_stackmap(content):
    r = R(content)
    n_entries = r.u2()
    w = W()
    w.u2(n_entries)
    for i in range(n_entries):
        offset = r.u2()
        nloc = r.u2()
        locals_ = [parse_vti(r) for _ in range(nloc)]
        nstk = r.u2()
        stack = [parse_vti(r) for _ in range(nstk)]
        if nloc > 255 or nstk > 255:
            raise Exception('entry %d: nloc=%d or nstk=%d >255'
                            % (i, nloc, nstk))
        w.u2(offset)
        w.u1(nloc)
        for vti in locals_:
            write_vti(w, vti)
        w.u1(nstk)
        for vti in stack:
            write_vti(w, vti)
    return bytes(w.b)


def parse_and_rebuild(data):
    r = R(data)
    magic = r.u4()
    minor = r.u2()
    major = r.u2()
    cp = parse_cp(r)
    access = r.u2()
    this_c = r.u2()
    super_c = r.u2()
    nint = r.u2()
    interfaces = [r.u2() for _ in range(nint)]
    fields = []
    nfield = r.u2()
    for _ in range(nfield):
        fa = r.u2()
        fni = r.u2()
        fdi = r.u2()
        nfa = r.u2()
        fattrs = [(r.u2(), r.take(r.u4())) for _ in range(nfa)]
        fields.append((fa, fni, fdi, fattrs))
    methods = []
    converted = 0
    nmeth = r.u2()
    for _ in range(nmeth):
        ma = r.u2()
        mni = r.u2()
        mdi = r.u2()
        nma = r.u2()
        meth_attrs = []
        for _ in range(nma):
            ani = r.u2()
            alen = r.u4()
            acontent = r.take(alen)
            aname = cp_name(cp, ani)
            if aname == 'Code':
                cr = R(acontent)
                ms = cr.u2()
                ml = cr.u2()
                clen = cr.u4()
                code = cr.take(clen)
                net = cr.u2()
                exctab = cr.take(net * 8)
                nsub = cr.u2()
                subattrs = []
                for _ in range(nsub):
                    sni = cr.u2()
                    slen = cr.u4()
                    scontent = cr.take(slen)
                    sname = cp_name(cp, sni)
                    if sname == 'StackMap':
                        subattrs.append((sni, convert_stackmap(scontent)))
                        converted += 1
                    else:
                        subattrs.append((sni, scontent))
                cw = W()
                cw.u2(ms)
                cw.u2(ml)
                cw.u4(len(code))
                cw.raw(code)
                cw.u2(net)
                cw.raw(exctab)
                cw.u2(len(subattrs))
                for sni, sc in subattrs:
                    cw.u2(sni)
                    cw.u4(len(sc))
                    cw.raw(sc)
                meth_attrs.append((ani, bytes(cw.b)))
            else:
                meth_attrs.append((ani, acontent))
        methods.append((ma, mni, mdi, meth_attrs))
    nca = r.u2()
    cls_attrs = [(r.u2(), r.take(r.u4())) for _ in range(nca)]
    w = W()
    w.u4(magic)
    w.u2(minor)
    w.u2(major)
    write_cp(w, cp)
    w.u2(access)
    w.u2(this_c)
    w.u2(super_c)
    w.u2(len(interfaces))
    for i in interfaces:
        w.u2(i)
    w.u2(len(fields))
    for fa, fni, fdi, fattrs in fields:
        w.u2(fa)
        w.u2(fni)
        w.u2(fdi)
        w.u2(len(fattrs))
        for ani, ac in fattrs:
            w.u2(ani)
            w.u4(len(ac))
            w.raw(ac)
    w.u2(len(methods))
    for ma, mni, mdi, mattrs in methods:
        w.u2(ma)
        w.u2(mni)
        w.u2(mdi)
        w.u2(len(mattrs))
        for ani, ac in mattrs:
            w.u2(ani)
            w.u4(len(ac))
            w.raw(ac)
    w.u2(len(cls_attrs))
    for ani, ac in cls_attrs:
        w.u2(ani)
        w.u4(len(ac))
        w.raw(ac)
    return bytes(w.b), converted


def main(in_jar, out_jar):
    zin = zipfile.ZipFile(in_jar, 'r')
    zout = zipfile.ZipFile(out_jar, 'w', zipfile.ZIP_DEFLATED)
    total = 0
    for info in zin.infolist():
        data = zin.read(info.filename)
        if info.filename.endswith('.class'):
            new_data, conv = parse_and_rebuild(data)
            total += conv
            data = new_data
        zout.writestr(info, data)
    zin.close()
    zout.close()
    print('Converted %d StackMap attributes (u2->u1 CLDC)' % total)
    print('Output: %s (%d bytes)' % (out_jar, os.path.getsize(out_jar)))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
