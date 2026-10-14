package com.nokia.mid.ui;

import javax.microedition.lcdui.Graphics;

/**
 * Minimal Nokia UI API DirectUtils replacement.
 *
 * Real Nokia devices handed out a DirectGraphics bound to a Graphics;
 * here we return a small MIDP-based implementation that supports the
 * subset declared in {@link DirectGraphics}.
 */
public class DirectUtils {

    public static DirectGraphics getDirectGraphics(Graphics g) {
        return new DirectGraphicsImpl(g);
    }
}
