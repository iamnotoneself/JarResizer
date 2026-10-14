package jr.file;

import java.io.IOException;
import java.util.Enumeration;
import java.util.Vector;

import javax.microedition.io.ConnectionNotFoundException;

/**
 * Relocated JSR-75 FileSystemRegistry. Reports one virtual root backed
 * by the in-memory file system.
 */
public class FileSystemRegistry {

    private static final String ROOT = "root/";

    private FileSystemRegistry() {
    }

    public static Enumeration roots() {
        Vector v = new Vector();
        v.addElement(ROOT);
        return v.elements();
    }

    public static FileConnection openFileConnection(String url)
            throws ConnectionNotFoundException, IOException {
        if (url == null) {
            throw new ConnectionNotFoundException("null url");
        }
        return new FakeFileConnection(url);
    }

    public static void addFileSystemListener(FileSystemListener listener) {
        // RAM roots never mount/unmount
    }

    public static void removeFileSystemListener(FileSystemListener listener) {
        // RAM roots never mount/unmount
    }
}
