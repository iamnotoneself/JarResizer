package jr.sensor;

/** One int-valued reading of one accelerometer channel. */
public class FakeData implements Data {

    private final ChannelInfo info;
    private final int[] values;

    public FakeData(ChannelInfo info, int value) {
        this.info = info;
        this.values = new int[] { value };
    }

    public ChannelInfo getChannelInfo() {
        return info;
    }

    public int[] getIntValues() {
        return values;
    }
}
