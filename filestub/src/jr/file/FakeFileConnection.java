package jr.file;

import java.io.ByteArrayInputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Enumeration;
import java.util.Hashtable;
import java.util.Vector;

/**
 * In-memory implementation of the relocated JSR-75 FileConnection.
 *
 * BlackBerry 7.1 has no javax.microedition.io.file package in the compile
 * library and custom javax.* classes are forbidden, so games that persist
 * saves through JSR-75 get a process-local RAM file system instead:
 * reads/writes succeed, listings work, but data is not kept across MIDlet
 * restarts (games treat it like a freshly formatted card).
 *
 * Keys are absolute paths, e.g. "/root/save/game.dat" (files) or
 * "/root/save/" (directories are implicit, derived from file keys).
 */
public final class FakeFileConnection implements FileConnection {

    static final class Entry {
        byte[] data = new byte[0];
        long modified = 0;
    }

    // process-local "memory card"
    private static final Hashtable FILES = new Hashtable();

    private static final long CARD_SIZE = 64L * 1024L * 1024L;

    private String host;
    private String path;
    private String dirPath;
    private String name;
    private boolean directory;
    private boolean open = true;

    public FakeFileConnection(String url) {
        relocate(url);
    }

    private void relocate(String url) {
        String u = url;
        int idx = u.indexOf("://");
        String rest = (idx >= 0) ? u.substring(idx + 3) : u;
        int slash = rest.indexOf('/');
        if (slash < 0) {
            host = rest;
            path = "/";
        } else {
            host = rest.substring(0, slash);
            path = rest.substring(slash);
        }
        if (path.length() == 0) {
            path = "/";
        }
        directory = path.endsWith("/");
        if (directory) {
            dirPath = path;
            name = "";
        } else {
            int s = path.lastIndexOf('/');
            dirPath = path.substring(0, s + 1);
            name = path.substring(s + 1);
        }
    }

    private String key() {
        return host.length() > 0 ? "/" + host + path : path;
    }

    private Entry entry() {
        return (Entry) FILES.get(key());
    }

    private void checkOpen() throws ConnectionClosedException {
        if (!open) {
            throw new ConnectionClosedException();
        }
    }

    // ---- structural metadata ------------------------------------------------

    public boolean isOpen() {
        return open;
    }

    public void close() throws IOException {
        open = false;
    }

    public String getName() {
        return name;
    }

    public String getPath() {
        return dirPath;
    }

    public String getURL() {
        return "file://" + host + path;
    }

    public boolean isDirectory() {
        return directory;
    }

    public boolean exists() {
        return !directory && FILES.containsKey(key());
    }

    public boolean isHidden() {
        return false;
    }

    public boolean canRead() {
        return true;
    }

    public boolean canWrite() {
        return true;
    }

    // ---- create / delete / rename / mkdir -----------------------------------

    public void create() throws IOException {
        checkOpen();
        if (directory) {
            throw new IOException("create() on a directory: " + getURL());
        }
        if (FILES.containsKey(key())) {
            throw new IOException("file already exists: " + getURL());
        }
        Entry e = new Entry();
        e.modified = System.currentTimeMillis();
        FILES.put(key(), e);
    }

    public void mkdir() throws IOException {
        checkOpen();
        // directories are implicit in the RAM file system
    }

    public void delete() throws IOException {
        checkOpen();
        if (directory) {
            String prefix = key();
            Vector victims = new Vector();
            for (Enumeration en = FILES.keys(); en.hasMoreElements();) {
                String k = (String) en.nextElement();
                if (k.startsWith(prefix)) {
                    victims.addElement(k);
                }
            }
            for (Enumeration en = victims.elements(); en.hasMoreElements();) {
                FILES.remove(en.nextElement());
            }
            return;
        }
        if (FILES.remove(key()) == null) {
            throw new IOException("no such file: " + getURL());
        }
    }

    public void rename(String newName) throws IOException {
        checkOpen();
        if (directory || newName == null
                || newName.length() == 0 || newName.indexOf('/') >= 0) {
            throw new IOException("bad rename target: " + newName);
        }
        Entry e = entry();
        if (e == null) {
            throw new IOException("no such file: " + getURL());
        }
        String newKey = (host.length() > 0 ? "/" + host : "")
                + dirPath + newName;
        FILES.remove(key());
        FILES.put(newKey, e);
        name = newName;
        path = dirPath + newName;
    }

    public void setFileConnection(String itemName) throws IOException {
        checkOpen();
        if (itemName == null) {
            throw new NullPointerException("itemName is null");
        }
        open = true;
        relocate("file://" + host + dirPath + itemName);
    }

    // ---- attributes ---------------------------------------------------------

    public void setHidden(boolean hidden) throws IOException {
        checkOpen();
    }

    public void setReadable(boolean readable) throws IOException {
        checkOpen();
    }

    public void setWritable(boolean writable) throws IOException {
        checkOpen();
    }

    public long fileSize() throws IOException {
        checkOpen();
        Entry e = entry();
        if (e == null) {
            throw new IOException("no such file: " + getURL());
        }
        return e.data.length;
    }

    public long directorySize(boolean includeSubDirs) throws IOException {
        checkOpen();
        String prefix = key();
        long total = 0;
        for (Enumeration en = FILES.keys(); en.hasMoreElements();) {
            String k = (String) en.nextElement();
            if (k.startsWith(prefix)) {
                total += ((Entry) FILES.get(k)).data.length;
            }
        }
        return total;
    }

    public long lastModified() {
        Entry e = entry();
        return e == null ? 0L : e.modified;
    }

    public long availableSize() {
        return CARD_SIZE - usedSize();
    }

    public long totalSize() {
        return CARD_SIZE;
    }

    public long usedSize() {
        long total = 0;
        for (Enumeration en = FILES.elements(); en.hasMoreElements();) {
            total += ((Entry) en.nextElement()).data.length;
        }
        return total;
    }

    // ---- listing ------------------------------------------------------------

    public Enumeration list() throws IOException {
        return list(null, false);
    }

    public Enumeration list(String filter, boolean includeHidden)
            throws IOException {
        checkOpen();
        Vector result = new Vector();
        if (!directory) {
            return result.elements();
        }
        String prefix = key();
        Hashtable seen = new Hashtable();
        for (Enumeration en = FILES.keys(); en.hasMoreElements();) {
            String k = (String) en.nextElement();
            if (!k.startsWith(prefix)) {
                continue;
            }
            String rest = k.substring(prefix.length());
            int slash = rest.indexOf('/');
            if (slash < 0) {
                // direct child file
                if (rest.length() > 0 && match(rest, filter)) {
                    if (!seen.containsKey(rest)) {
                        seen.put(rest, rest);
                        result.addElement(rest);
                    }
                }
            } else {
                // child directory inferred from a descendant file
                String sub = rest.substring(0, slash + 1);
                if (match(sub, filter) && !seen.containsKey(sub)) {
                    seen.put(sub, sub);
                    result.addElement(sub);
                }
            }
        }
        return result.elements();
    }

    /** Minimal JSR-75 wildcard match: '*' runs, '?' single char. */
    private static boolean match(String name, String filter) {
        if (filter == null || filter.length() == 0 || "*".equals(filter)) {
            return true;
        }
        return wildcard(name, filter, 0, 0);
    }

    private static boolean wildcard(String s, String p, int si, int pi) {
        int sl = s.length();
        int pl = p.length();
        while (si < sl) {
            char pc = pi < pl ? p.charAt(pi) : 0;
            if (pc == '*') {
                while (pi < pl && p.charAt(pi) == '*') {
                    pi++;
                }
                if (pi == pl) {
                    return true;
                }
                for (int k = si; k <= sl; k++) {
                    if (wildcard(s, p, k, pi)) {
                        return true;
                    }
                }
                return false;
            }
            if (pi >= pl || (pc != '?' && pc != s.charAt(si))) {
                return false;
            }
            si++;
            pi++;
        }
        while (pi < pl && p.charAt(pi) == '*') {
            pi++;
        }
        return pi == pl;
    }

    // ---- streams ------------------------------------------------------------

    public InputStream openInputStream() throws IOException {
        checkOpen();
        Entry e = entry();
        if (e == null) {
            throw new IOException("no such file: " + getURL());
        }
        byte[] copy = new byte[e.data.length];
        System.arraycopy(e.data, 0, copy, 0, e.data.length);
        return new ByteArrayInputStream(copy);
    }

    public DataInputStream openDataInputStream() throws IOException {
        return new DataInputStream(openInputStream());
    }

    public OutputStream openOutputStream() throws IOException {
        return openOutputStream(0L);
    }

    public OutputStream openOutputStream(long byteOffset) throws IOException {
        checkOpen();
        if (directory) {
            throw new IOException("openOutputStream() on a directory: "
                    + getURL());
        }
        Entry e = entry();
        if (e == null) {
            // be forgiving: many games forget create() before writing
            e = new Entry();
            FILES.put(key(), e);
        }
        int off = (int) Math.max(0L, Math.min(byteOffset,
                (long) e.data.length));
        byte[] seed = new byte[off];
        System.arraycopy(e.data, 0, seed, 0, off);
        return new MemOutputStream(key(), seed);
    }

    public DataOutputStream openDataOutputStream() throws IOException {
        return new DataOutputStream(openOutputStream());
    }

    public void truncate(long byteOffset) throws IOException {
        checkOpen();
        Entry e = entry();
        if (e == null) {
            throw new IOException("no such file: " + getURL());
        }
        int off = (int) Math.max(0L, Math.min(byteOffset,
                (long) e.data.length));
        byte[] cut = new byte[off];
        System.arraycopy(e.data, 0, cut, 0, off);
        e.data = cut;
        e.modified = System.currentTimeMillis();
    }

    /** Gathers writes and commits them to the RAM file on flush/close. */
    private static final class MemOutputStream extends OutputStream {

        private final String fileKey;
        private byte[] buf;
        private int count;
        private boolean closed = false;

        MemOutputStream(String key, byte[] seed) {
            fileKey = key;
            buf = new byte[Math.max(32, seed.length)];
            System.arraycopy(seed, 0, buf, 0, seed.length);
            count = seed.length;
        }

        private void ensure(int need) {
            if (need <= buf.length) {
                return;
            }
            int n = buf.length;
            while (n < need) {
                n <<= 1;
            }
            byte[] grown = new byte[n];
            System.arraycopy(buf, 0, grown, 0, count);
            buf = grown;
        }

        public void write(int b) throws IOException {
            if (closed) {
                throw new IOException("stream closed");
            }
            ensure(count + 1);
            buf[count++] = (byte) b;
        }

        public void write(byte[] b, int off, int len) throws IOException {
            if (closed) {
                throw new IOException("stream closed");
            }
            if (b == null) {
                throw new NullPointerException();
            }
            ensure(count + len);
            System.arraycopy(b, off, buf, count, len);
            count += len;
        }

        public void flush() throws IOException {
            commit();
        }

        public void close() throws IOException {
            if (!closed) {
                commit();
                closed = true;
            }
        }

        private void commit() {
            Entry e = (Entry) FILES.get(fileKey);
            if (e == null) {
                e = new Entry();
                FILES.put(fileKey, e);
            }
            byte[] out = new byte[count];
            System.arraycopy(buf, 0, out, 0, count);
            e.data = out;
            e.modified = System.currentTimeMillis();
        }
    }
}
