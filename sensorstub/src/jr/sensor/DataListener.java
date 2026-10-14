package jr.sensor;

/** Minimal JSR-256 DataListener interface (tilt/sensor data callback). */
public interface DataListener {

    void dataReceived(SensorConnection sensor, Data[] data,
                      boolean isDataLost);
}
