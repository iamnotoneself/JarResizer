package jr.sensor;

/** Minimal JSR-256 SensorInfo interface subset used by ported games. */
public interface SensorInfo {

    ChannelInfo[] getChannelInfos();

    int getConnectionType();

    String getContextType();

    String getDescription();

    int getMaxBufferSize();

    String getModel();

    Object getProperty(String name);

    String[] getPropertyNames();

    String getQuantity();

    String getUrl();

    boolean isAvailabilityPushSupported();

    boolean isAvailable();

    boolean isConditionPushSupported();
}
