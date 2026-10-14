package com.nokia.mid.ui;

/**
 * No-op subset of com.nokia.mid.ui.DeviceControl (lights / vibration).
 * BlackBerry devices handle power state themselves; silently ignoring
 * these calls is enough for games that use them as effects.
 */
public class DeviceControl {

    public static void setLights(int num, int level) {
    }

    public static void flashLights(long duration) {
    }

    public static void startVibra(int frequency, long duration) {
    }

    public static void stopVibra() {
    }
}
