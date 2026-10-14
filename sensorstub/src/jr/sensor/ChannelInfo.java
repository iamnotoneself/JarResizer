package jr.sensor;

/** Minimal JSR-256 ChannelInfo interface subset used by ported games. */
public interface ChannelInfo {

    int getDataType();

    int getScale();

    Unit getUnit();

    String getName();

    float getAccuracy();

    MeasurementRange[] getMeasurementRanges();
}
