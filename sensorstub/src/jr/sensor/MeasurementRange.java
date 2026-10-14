package jr.sensor;

/** Minimal JSR-256 MeasurementRange stub (relocated package, see Unit). */
public class MeasurementRange {

    private final double smallest;
    private final double largest;
    private final double resolution;

    public MeasurementRange(double smallestValue, double largestValue,
                            double resolution) {
        this.smallest = smallestValue;
        this.largest = largestValue;
        this.resolution = resolution;
    }

    public double getSmallestValue() {
        return smallest;
    }

    public double getLargestValue() {
        return largest;
    }

    public double getResolution() {
        return resolution;
    }
}
