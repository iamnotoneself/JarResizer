package jr.sensor;

import javax.microedition.lcdui.Display;
import javax.microedition.lcdui.Displayable;
import javax.microedition.lcdui.game.GameCanvas;
import javax.microedition.midlet.MIDlet;

/**
 * Replacement JSR-256 SensorManager (relocated package).
 *
 * - findSensors reports one fake 3-axis accelerometer so tilt games pass
 *   their sensor discovery / sampling requirements.
 * - open() is the rewritten target of javax.microedition.io.Connector
 *   open: sensor: URLs return the fake connection, everything else is
 *   delegated to the real GCF Connector (HTTP uploads still work).
 *
 * CLDC 1.1 has NO reflection (Class.getField/getMethod do not exist): the
 * MIDlet is captured structurally from the setDataListener(listener)
 * argument, which for J2ME games is the MIDlet itself.
 */
public class SensorManager {

    private static final SensorInfo[] ACCEL = new SensorInfo[] {
        new FakeSensorInfo()
    };

    private static MIDlet midlet;
    private static int pulseX = 0;
    private static int pulseTicks = 0;

    public static SensorInfo[] findSensors(String quantity,
                                           String contextType) {
        return ACCEL;
    }

    public static javax.microedition.io.Connection open(String url)
            throws java.io.IOException {
        if (url != null && url.startsWith("sensor")) {
            return new FakeSensorConnection();
        }
        return javax.microedition.io.Connector.open(url);
    }

    /** Capture the MIDlet from the setDataListener listener argument. */
    public static void bindMIDlet(Object listener) {
        if (midlet == null && listener instanceof MIDlet) {
            midlet = (MIDlet) listener;
        }
    }

    /** Poll latched game keys on the visible canvas (100 ms sampler). */
    public static void pollKeys() {
        if (midlet == null) {
            return;
        }
        try {
            Display d = Display.getDisplay(midlet);
            if (d == null) {
                return;
            }
            Displayable cur = d.getCurrent();
            if (!(cur instanceof GameCanvas)) {
                return;
            }
            int s = ((GameCanvas) cur).getKeyStates();
            if ((s & (GameCanvas.UP_PRESSED
                    | GameCanvas.FIRE_PRESSED)) != 0) {
                pulse(600);
            } else if ((s & GameCanvas.DOWN_PRESSED) != 0) {
                pulse(40);
            }
        } catch (Throwable t) {
            // not visible / not a game canvas: keep sampling
        }
    }

    /** Injected Canvas.keyPressed hook target (works on canvases that
     *  still receive key callbacks). */
    public static void keyPressed(int key) {
        switch (key) {
            case 'w': case 'W': case '2': case ' ': case 13:
            case -1: case -5:
                pulse(600);
                break;
            case 's': case 'S': case '8': case -2:
                pulse(40);
                break;
            default:
                break;
        }
    }

    static synchronized void pulse(int magnitude) {
        pulseX = magnitude;
        pulseTicks = 1;
    }

    static synchronized int consumePulse() {
        if (pulseTicks > 0) {
            pulseTicks--;
            return pulseX;
        }
        return 0;
    }
}
