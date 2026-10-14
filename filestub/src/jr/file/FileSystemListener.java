package jr.file;

/**
 * Relocated JSR-75 FileSystemListener. Root mount/unmount events never
 * fire on the in-memory file system, so this is a signalling interface
 * only.
 */
public interface FileSystemListener {

    int ROOT_ADDED = 0;
    int ROOT_REMOVED = 1;

    void rootChanged(int state, String rootName);
}
