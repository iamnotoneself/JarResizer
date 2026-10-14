package jr.sensor;

/**
 * Fake accelerometer connection: a background thread pushes one
 * 3-channel int sample every 100 ms. Baseline is upright (0,0,1000 mG);
 * pollKeys()/keyPressed() arm a one-tick X pulse emulating a shake.
 */
public class FakeSensorConnection implements SensorConnection, Runnable {

    private DataListener listener;
    private boolean alive = true;
    private Thread thread;

    public void setDataListener(DataListener l, int bufferSize) {
        this.listener = l;
        SensorManager.bindMIDlet(l);
        if (thread == null) {
            thread = new Thread(this);
            thread.start();
        }
    }

    public void removeDataListener() {
        this.listener = null;
    }

    public void close() {
        alive = false;
        listener = null;
        if (thread != null) {
            thread.interrupt();
        }
    }

    public void run() {
        ChannelInfo[] ch = FakeSensorInfo.CHANNELS;
        while (alive) {
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                return;
            }
            DataListener l = listener;
            if (l == null) {
                continue;
            }
            SensorManager.pollKeys();
            int px = SensorManager.consumePulse();
            Data[] data = new Data[] {
                new FakeData(ch[0], px),
                new FakeData(ch[1], 0),
                new FakeData(ch[2], 1000),
            };
            try {
                l.dataReceived(this, data, false);
            } catch (Throwable t) {
                // a game-side hiccup must never kill the sampler thread
            }
        }
    }
}
