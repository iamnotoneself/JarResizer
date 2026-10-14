package jr.jsec;

/**
 * Self-contained MD5 MessageDigest for BlackBerry CLDC, which lacks
 * java.security.MessageDigest (games use it for score-signing MD5).
 * Relocated to jr.jsec because BlackBerry forbids app-defined classes
 * under java.* / javax.* packages.
 *
 * API subset mirrors the compact (J2ME-style) signatures used by games:
 *   static MessageDigest getInstance(String algorithm)
 *   void reset()
 *   void update(byte[] input, int offset, int len)
 *   int  digest(byte[] buf, int offset, int len)  // returns bytes written
 */
public abstract class MessageDigest {

    protected MessageDigest() {
        // no state here; subclasses initialise their own fields and must
        // NOT rely on an overridable reset() being called by super()
        // (subclass fields are still null during the super constructor)
    }

    public static MessageDigest getInstance(String algorithm) {
        // Only MD5 is needed by the ported games; unknown algorithms are
        // served MD5 as well rather than failing to load.
        return new MD5Digest();
    }

    public void reset() {
        // overridden by the implementation to re-seed state
    }

    public void update(byte[] input, int offset, int len) {
        // overridden
    }

    public int digest(byte[] buf, int offset, int len) {
        // overridden; returns number of bytes written
        return 0;
    }

    // --------------------------------------------------------------- MD5

    private static final class MD5Digest extends MessageDigest {

        private int[] st;
        private long cnt;
        private byte[] buf;
        private final int[] x = new int[16];

        MD5Digest() {
            st = new int[4];
            buf = new byte[64];
            reset();
        }

        public void reset() {
            st[0] = 0x67452301;
            st[1] = 0xefcdab89;
            st[2] = 0x98badcfe;
            st[3] = 0x10325476;
            cnt = 0;
        }

        public void update(byte[] input, int offset, int len) {
            int pos = (int) (cnt & 63);
            cnt += len;
            int i = 0;
            if (pos != 0) {
                int fill = 64 - pos;
                if (len < fill) {
                    System.arraycopy(input, offset, buf, pos, len);
                    return;
                }
                System.arraycopy(input, offset, buf, pos, fill);
                transform(buf, 0);
                i = fill;
            }
            for (; i + 64 <= len; i += 64) {
                transform(input, offset + i);
            }
            if (i < len) {
                System.arraycopy(input, offset + i, buf, 0, len - i);
            }
        }

        public int digest(byte[] out, int offset, int len) {
            if (len < 16) {
                return 0;
            }
            int pos = (int) (cnt & 63);
            buf[pos++] = (byte) 0x80;
            if (pos > 56) {
                for (int i = pos; i < 64; i++) {
                    buf[i] = 0;
                }
                transform(buf, 0);
                pos = 0;
            }
            for (int i = pos; i < 56; i++) {
                buf[i] = 0;
            }
            long bits = cnt << 3;
            for (int i = 0; i < 8; i++) {
                buf[56 + i] = (byte) (bits >>> (8 * i));
            }
            transform(buf, 0);
            for (int i = 0; i < 4; i++) {
                int v = st[i];
                out[offset + i * 4] = (byte) (v & 0xff);
                out[offset + i * 4 + 1] = (byte) ((v >>> 8) & 0xff);
                out[offset + i * 4 + 2] = (byte) ((v >>> 16) & 0xff);
                out[offset + i * 4 + 3] = (byte) ((v >>> 24) & 0xff);
            }
            reset();
            return 16;
        }

        private void transform(byte[] b, int off) {
            for (int i = 0; i < 16; i++) {
                int p = off + i * 4;
                x[i] = (b[p] & 0xff) | ((b[p + 1] & 0xff) << 8)
                        | ((b[p + 2] & 0xff) << 16) | (b[p + 3] << 24);
            }
            int a = st[0], c = st[1], d = st[2], e = st[3];

            // Round 1
            a = ff(a, c, d, e, x[0], 7, 0xd76aa478);
            e = ff(e, a, c, d, x[1], 12, 0xe8c7b756);
            d = ff(d, e, a, c, x[2], 17, 0x242070db);
            c = ff(c, d, e, a, x[3], 22, 0xc1bdceee);
            a = ff(a, c, d, e, x[4], 7, 0xf57c0faf);
            e = ff(e, a, c, d, x[5], 12, 0x4787c62a);
            d = ff(d, e, a, c, x[6], 17, 0xa8304613);
            c = ff(c, d, e, a, x[7], 22, 0xfd469501);
            a = ff(a, c, d, e, x[8], 7, 0x698098d8);
            e = ff(e, a, c, d, x[9], 12, 0x8b44f7af);
            d = ff(d, e, a, c, x[10], 17, 0xffff5bb1);
            c = ff(c, d, e, a, x[11], 22, 0x895cd7be);
            a = ff(a, c, d, e, x[12], 7, 0x6b901122);
            e = ff(e, a, c, d, x[13], 12, 0xfd987193);
            d = ff(d, e, a, c, x[14], 17, 0xa679438e);
            c = ff(c, d, e, a, x[15], 22, 0x49b40821);

            // Round 2
            a = gg(a, c, d, e, x[1], 5, 0xf61e2562);
            e = gg(e, a, c, d, x[6], 9, 0xc040b340);
            d = gg(d, e, a, c, x[11], 14, 0x265e5a51);
            c = gg(c, d, e, a, x[0], 20, 0xe9b6c7aa);
            a = gg(a, c, d, e, x[5], 5, 0xd62f105d);
            e = gg(e, a, c, d, x[10], 9, 0x02441453);
            d = gg(d, e, a, c, x[15], 14, 0xd8a1e681);
            c = gg(c, d, e, a, x[4], 20, 0xe7d3fbc8);
            a = gg(a, c, d, e, x[9], 5, 0x21e1cde6);
            e = gg(e, a, c, d, x[14], 9, 0xc33707d6);
            d = gg(d, e, a, c, x[3], 14, 0xf4d50d87);
            c = gg(c, d, e, a, x[8], 20, 0x455a14ed);
            a = gg(a, c, d, e, x[13], 5, 0xa9e3e905);
            e = gg(e, a, c, d, x[2], 9, 0xfcefa3f8);
            d = gg(d, e, a, c, x[7], 14, 0x676f02d9);
            c = gg(c, d, e, a, x[12], 20, 0x8d2a4c8a);

            // Round 3
            a = hh(a, c, d, e, x[5], 4, 0xfffa3942);
            e = hh(e, a, c, d, x[8], 11, 0x8771f681);
            d = hh(d, e, a, c, x[11], 16, 0x6d9d6122);
            c = hh(c, d, e, a, x[14], 23, 0xfde5380c);
            a = hh(a, c, d, e, x[1], 4, 0xa4beea44);
            e = hh(e, a, c, d, x[4], 11, 0x4bdecfa9);
            d = hh(d, e, a, c, x[7], 16, 0xf6bb4b60);
            c = hh(c, d, e, a, x[10], 23, 0xbebfbc70);
            a = hh(a, c, d, e, x[13], 4, 0x289b7ec6);
            e = hh(e, a, c, d, x[0], 11, 0xeaa127fa);
            d = hh(d, e, a, c, x[3], 16, 0xd4ef3085);
            c = hh(c, d, e, a, x[6], 23, 0x04881d05);
            a = hh(a, c, d, e, x[9], 4, 0xd9d4d039);
            e = hh(e, a, c, d, x[12], 11, 0xe6db99e5);
            d = hh(d, e, a, c, x[15], 16, 0x1fa27cf8);
            c = hh(c, d, e, a, x[2], 23, 0xc4ac5665);

            // Round 4
            a = ii(a, c, d, e, x[0], 6, 0xf4292244);
            e = ii(e, a, c, d, x[7], 10, 0x432aff97);
            d = ii(d, e, a, c, x[14], 15, 0xab9423a7);
            c = ii(c, d, e, a, x[5], 21, 0xfc93a039);
            a = ii(a, c, d, e, x[12], 6, 0x655b59c3);
            e = ii(e, a, c, d, x[3], 10, 0x8f0ccc92);
            d = ii(d, e, a, c, x[10], 15, 0xffeff47d);
            c = ii(c, d, e, a, x[1], 21, 0x85845dd1);
            a = ii(a, c, d, e, x[8], 6, 0x6fa87e4f);
            e = ii(e, a, c, d, x[15], 10, 0xfe2ce6e0);
            d = ii(d, e, a, c, x[6], 15, 0xa3014314);
            c = ii(c, d, e, a, x[13], 21, 0x4e0811a1);
            a = ii(a, c, d, e, x[4], 6, 0xf7537e82);
            e = ii(e, a, c, d, x[11], 10, 0xbd3af235);
            d = ii(d, e, a, c, x[2], 15, 0x2ad7d2bb);
            c = ii(c, d, e, a, x[9], 21, 0xeb86d391);

            st[0] += a;
            st[1] += c;
            st[2] += d;
            st[3] += e;
        }

        private static int rol(int v, int s) {
            return (v << s) | (v >>> (32 - s));
        }

        private static int ff(int a, int b, int c, int d, int x,
                              int s, int ac) {
            return b + rol(a + ((b & c) | (~b & d)) + x + ac, s);
        }

        private static int gg(int a, int b, int c, int d, int x,
                              int s, int ac) {
            return b + rol(a + ((b & d) | (c & ~d)) + x + ac, s);
        }

        private static int hh(int a, int b, int c, int d, int x,
                              int s, int ac) {
            return b + rol(a + (b ^ c ^ d) + x + ac, s);
        }

        private static int ii(int a, int b, int c, int d, int x,
                              int s, int ac) {
            return b + rol(a + (c ^ (b | ~d)) + x + ac, s);
        }
    }
}
