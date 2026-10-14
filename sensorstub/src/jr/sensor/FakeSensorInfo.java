package jr.sensor;

/** A single fake embedded 3-axis accelerometer ("acceleration"). */
public class FakeSensorInfo implements SensorInfo {

    public static final String URL =
            "sensor:acceleration;contextType=user";

    static final ChannelInfo[] CHANNELS = new ChannelInfo[] {
        new FakeChannelInfo("X-axis"),
        new FakeChannelInfo("Y-axis"),
        new FakeChannelInfo("Z-axis"),
    };

    public ChannelInfo[] getChannelInfos() {
        return CHANNELS;
    }

    public int getConnectionType() {
        return 1; // embedded
    }

    public String getContextType() {
        return "user";
    }

    public String getDescription() {
        return "BlackBerry keyboard tilt emulation";
    }

    public int getMaxBufferSize() {
        return 16;
    }

    public String getModel() {
        return "jr-tilt";
    }

    public Object getProperty(String name) {
        return null;
    }

    public String[] getPropertyNames() {
        return new String[0];
    }

    public String getQuantity() {
        return "acceleration";
    }

    public String getUrl() {
        return URL;
    }

    public boolean isAvailabilityPushSupported() {
        return false;
    }

    public boolean isAvailable() {
        return true;
    }

    public boolean isConditionPushSupported() {
        return false;
    }
}
