package com.nokia.mid.ui;

/**
 * No-op subset of the Nokia touch-device haptics API
 * (com.nokia.mid.ui.TactileFeedback). Feedback constants are inlined by
 * the compiler at call sites, so only directFeedback(int) is needed to
 * link; the vibration effect itself is ignored.
 */
public class TactileFeedback {

    public static final int FEEDBACK_NONE = 0;
    public static final int FEEDBACK_STYLUS_TAP = 1;
    public static final int FEEDBACK_STYLUS_HOLD = 2;
    public static final int FEEDBACK_STYLUS_RELEASE = 3;
    public static final int FEEDBACK_VIRTUAL_KEY = 4;

    public TactileFeedback() {
    }

    public void directFeedback(int type) {
    }

    public static void triggerFeedback(int type) {
    }
}
