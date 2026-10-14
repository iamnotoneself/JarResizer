package com.nokia.mid.ui;

/**
 * Minimal subset of the Nokia UI API DirectGraphics interface.
 *
 * Only methods actually used by a given game need to be declared here:
 * invokeinterface resolution in the VM matches name + descriptor, so an
 * interface containing just the referenced methods is enough to link.
 *
 * Nokia UI API 1.1 fillPolygon signature (with array offsets):
 *   fillPolygon(xPoints, xOffset, yPoints, yOffset, nPoints, argbColor)
 */
public interface DirectGraphics {

    /** Keep the original orientation. */
    int FLIP_NONE = 0;

    /** Nokia UI pixel format: 16-bit 5-6-5 RGB (value inlined by games). */
    int TYPE_USHORT_565_RGB = 4444;

    void fillPolygon(int[] xPoints, int xOffset, int[] yPoints,
                     int yOffset, int nPoints, int argbColor);

    void drawPixels(short[] pixels, boolean transparency, int offset,
                    int scanLength, int x, int y, int width, int height,
                    int manipulation, int format);
}
