package com.nokia.mid.ui;

import javax.microedition.lcdui.Graphics;

/**
 * MIDP 2.0 implementation of the DirectGraphics subset.
 * Polygons are rendered as a triangle fan (works for convex polygons;
 * concave ones may differ slightly from the Nokia rasteriser).
 */
public class DirectGraphicsImpl implements DirectGraphics {

    private final Graphics g;

    DirectGraphicsImpl(Graphics g) {
        this.g = g;
    }

    public void fillPolygon(int[] xPoints, int xOffset, int[] yPoints,
                            int yOffset, int nPoints, int argbColor) {
        if (g == null || xPoints == null || yPoints == null || nPoints < 3) {
            return;
        }
        if (xOffset < 0 || yOffset < 0
                || xOffset + nPoints > xPoints.length
                || yOffset + nPoints > yPoints.length) {
            return;
        }
        // Graphics.setColor(int) takes 0xRRGGBB (low 24 bits of ARGB).
        g.setColor(argbColor);
        int x0 = xPoints[xOffset];
        int y0 = yPoints[yOffset];
        for (int i = 1; i < nPoints - 1; i++) {
            g.fillTriangle(x0, y0,
                    xPoints[xOffset + i], yPoints[yOffset + i],
                    xPoints[xOffset + i + 1], yPoints[yOffset + i + 1]);
        }
    }

    public void drawPixels(short[] pixels, boolean transparency, int offset,
                           int scanLength, int x, int y, int width, int height,
                           int manipulation, int format) {
        if (g == null || pixels == null || width <= 0 || height <= 0) {
            return;
        }
        int[] argb = new int[width * height];
        for (int row = 0; row < height; row++) {
            int src = offset + row * scanLength;
            int dst = row * width;
            for (int col = 0; col < width; col++) {
                short p = pixels[src + col];
                // 16-bit 5-6-5 RGB expanded to 0xAARRGGBB, fully opaque.
                argb[dst + col] = 0xFF000000
                        | (((p >> 11) & 0x1F) * 0xFF / 0x1F) << 16
                        | (((p >> 5) & 0x3F) * 0xFF / 0x3F) << 8
                        | ((p & 0x1F) * 0xFF / 0x1F);
            }
        }
        // The observed game only uses FLIP_NONE; other manipulations are
        // drawn untransformed rather than failing to link.
        g.drawRGB(argb, 0, width, x, y, width, height, true);
    }
}
