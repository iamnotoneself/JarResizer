package jr.sensor;

/** Minimal JSR-256 Data interface subset used by ported games. */
public interface Data {

    ChannelInfo getChannelInfo();

    int[] getIntValues();
}
