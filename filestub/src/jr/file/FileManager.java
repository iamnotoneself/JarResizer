package jr.file;

import java.io.IOException;

import javax.microedition.io.Connection;

/**
 * Rewritten target of javax.microedition.io.Connector.open(...):
 * file: URLs are served by the in-memory JSR-75 implementation, every
 * other scheme (http, socket, comm, ...) is delegated to the real GCF
 * Connector so network features keep working.
 */
public class FileManager {

    public static Connection open(String url) throws IOException {
        if (url != null && url.startsWith("file:")) {
            return new FakeFileConnection(url);
        }
        return javax.microedition.io.Connector.open(url);
    }

    public static Connection open(String url, int mode) throws IOException {
        if (url != null && url.startsWith("file:")) {
            return new FakeFileConnection(url);
        }
        return javax.microedition.io.Connector.open(url, mode);
    }

    public static Connection open(String url, int mode, boolean timeouts)
            throws IOException {
        if (url != null && url.startsWith("file:")) {
            return new FakeFileConnection(url);
        }
        return javax.microedition.io.Connector.open(url, mode, timeouts);
    }
}
