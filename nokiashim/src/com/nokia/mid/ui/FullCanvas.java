package com.nokia.mid.ui;

import javax.microedition.lcdui.Canvas;
import javax.microedition.lcdui.Graphics;

/**
 * Minimal stand-in for the Nokia UI API FullCanvas.
 *
 * Games that subclassed FullCanvas are rewritten by the converter to extend
 * jr.ScaleCanvas instead; this class exists only so that any residual
 * constant-pool reference to FullCanvas (type checks, legacy entries) can
 * be resolved by the BlackBerry linker. It is a plain full-screen Canvas.
 */
public class FullCanvas extends Canvas {

    public FullCanvas() {
        super();
    }

    protected void paint(Graphics g) {
        // never used directly: games are reparented to jr.ScaleCanvas
    }
}
