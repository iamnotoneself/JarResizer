package jr.sensor;

/**
 * Minimal JSR-256 SensorConnection interface subset. Extends the GCF
 * Connection (a real javax.microedition system interface) so casts from
 * Connector.open(url) type-check; the interface itself lives in jr.sensor
 * because BlackBerry forbids app-defined classes under javax.*.
 */
public interface SensorConnection
        extends javax.microedition.io.Connection {

    void setDataListener(DataListener listener, int bufferSize);

    void removeDataListener();
}
