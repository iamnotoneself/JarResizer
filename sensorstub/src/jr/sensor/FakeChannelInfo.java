package jr.sensor;

/** Static descriptor for one fake accelerometer axis. */
public class FakeChannelInfo implements ChannelInfo {

    private final String name;

    public FakeChannelInfo(String name) {
        this.name = name;
    }

    public int getDataType() {
        return 1; // JSR-256 TYPE_INT
    }

    public int getScale() {
        return 0;
    }

    public Unit getUnit() {
        return new Unit("mG");
    }

    public String getName() {
        return name;
    }

    public float getAccuracy() {
        return 1.0f;
    }

    public MeasurementRange[] getMeasurementRanges() {
        return new MeasurementRange[] {
            new MeasurementRange(-2048.0, 2048.0, 1.0)
        };
    }
}
