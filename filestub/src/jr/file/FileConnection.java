package jr.file;

import java.io.IOException;
import java.io.OutputStream;
import java.util.Enumeration;

import javax.microedition.io.StreamConnection;

/**
 * Relocated JSR-75 (FileConnection 1.0) interface.
 *
 * The original interface lives at javax.microedition.io.file.FileConnection;
 * game bytecode references are rewritten to jr.file.FileConnection because
 * BlackBerry forbids application-defined javax.* classes and the BB 7.1
 * compile library does not ship the JSR-75 file package.
 *
 * Inherits openInputStream/openOutputStream/openDataInputStream/
 * openDataOutputStream/close from StreamConnection.
 */
public interface FileConnection extends StreamConnection {

    long availableSize();

    boolean canRead();

    boolean canWrite();

    void create() throws IOException;

    void delete() throws IOException;

    long directorySize(boolean includeSubDirs) throws IOException;

    boolean exists();

    long fileSize() throws IOException;

    String getName();

    String getPath();

    String getURL();

    boolean isDirectory();

    boolean isHidden();

    boolean isOpen();

    long lastModified();

    Enumeration list() throws IOException;

    Enumeration list(String filter, boolean includeHidden) throws IOException;

    void mkdir() throws IOException;

    OutputStream openOutputStream(long byteOffset) throws IOException;

    void rename(String newName) throws IOException;

    void setFileConnection(String itemName) throws IOException;

    void setHidden(boolean hidden) throws IOException;

    void setReadable(boolean readable) throws IOException;

    void setWritable(boolean writable) throws IOException;

    void truncate(long byteOffset) throws IOException;

    long totalSize();

    long usedSize();
}
