package jr;

import java.io.InputStream;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;
import javax.microedition.lcdui.game.GameCanvas;

/**
 * Injected screen-adaptation layer (J2ME MIDP 2.0 / CLDC 1.1).
 *
 * Extends GameCanvas (which itself extends Canvas), so it can back both
 * plain Canvas games and GameCanvas games. The transformer makes every
 * game canvas subclass extend this class and renames its
 * paint/pointer/getWidth/getHeight methods. The game keeps believing it
 * runs on its native design resolution (reported by getWidth/getHeight);
 * each frame is rendered into an offscreen buffer at that resolution and
 * nearest-neighbour scaled to the real screen, preserving aspect ratio
 * (letterboxed, centred). Pointer coordinates are mapped back.
 *
 * GameCanvas games obtain their Graphics from getGraphics(), which is
 * redirected at the native offscreen buffer here; every flushGraphics()
 * composites that buffer to the real display. getKeyStates() is
 * synthesised from the (translated) key events, so poll-based games keep
 * working on a full-QWERTY device.
 */
public abstract class ScaleCanvas extends GameCanvas {

    private final int nativeW;
    private final int nativeH;
    private final Image buffer;
    private final Graphics bg;
    private final int[] src;
    private int[] dst;
    private int dstW;
    private int dstH;
    // nearest-neighbour index lookup tables (avoid per-pixel idiv on
    // resource-constrained devices); rebuilt only when the scale changes
    private int[] mapX;
    private int[] mapY;
    private int mapSW;
    private int mapSH;
    // rotated-path lookup tables (screen index -> source col/row, -1 = crop)
    private int[] mapRX;
    private int[] mapRY;
    // direct stretch/cover tables (screen index -> source col/row, -1 crop)
    private int[] cvX;
    private int[] cvY;
    private int cvRW, cvRH, cvCw, cvCh, cvOx, cvOy, cvEH = -1;

    // fit policy (/jr-fit resource):
    //   0 = fit      letterbox, keep aspect ratio (default)
    //   1 = stretch  fill the whole screen, non-uniform scale
    //   2 = cover    fill the whole screen, uniform scale, crop overflow
    private int fitMode;
    // cached content geometry on the real screen
    private int gCw, gCh, gOx, gOy;
    private boolean gRot;
    private int gRW = -1, gRH = -1;

    // Visible sub-rectangle of the native buffer (/jr-crop = "x,y,w,h").
    // Some games reserve part of their frame for an on-screen keypad; only
    // this rectangle is treated as content. Defaults to the whole buffer.
    private int crX, crY, crW, crH;
    // Optional 5th jr-crop field ",1": per-frame detect the actual content
    // bottom (black rows vary between menu and gameplay) and stretch only
    // the populated region. effH = effective content height this frame.
    private boolean dynCrop;
    private int effH;
    private boolean srcReady;

    // ---- key translation (full-QWERTY device -> game's expected keys) ----
    // xlate[keyCode + KEY_OFF] == 0 means "pass through unchanged".
    // Covers negative standard/soft key codes (-16..) through ASCII (..511).
    private static final int KEY_OFF = 16;
    private static final int KEY_SPAN = 544;
    private final int[] xlate = new int[KEY_SPAN];

    // synthesised GameCanvas.getKeyStates() bits
    private int keysDown;
    private int keysLatched;

    // ---- on-screen virtual buttons (multi-touch gas/brake/steering) ----
    //
    // The platform delivers one (x,y) pointer stream; runtimes with real
    // multi-touch interleave several concurrent touches in that stream.
    // Touches are reconstructed heuristically: press -> new touch, drag ->
    // move the nearest touch, release -> drop the nearest touch. Each
    // touch is classified once at press time:
    //   KIND_KEY  - started on a virtual button; synthesises vendor key
    //               events (scKeyPressed/scKeyReleased + keyBits) so
    //               several buttons can be held at once;
    //   KIND_GAME - outside the buttons; forwarded to the game's pointer
    //               handlers as before;
    //   KIND_DEAD - captured by a button but holds no key.
    // Until two concurrent touches are seen the layer runs the single-
    // finger fallback layout: the bottom 35% of the screen split in
    // thirds - left = gas + steer left, middle = gas, right = gas + steer
    // right ("auto throttle + left/right third steering"). The first
    // overlapping press upgrades to the multi-button layout for good.
    private static final int KIND_GAME = 0;
    private static final int KIND_KEY = 1;
    private static final int KIND_DEAD = 2;
    private static final int MAXT = 8;
    private final int[] tX = new int[MAXT];
    private final int[] tY = new int[MAXT];
    private final int[] tK1 = new int[MAXT]; // key held by the touch (0=none)
    private final int[] tK2 = new int[MAXT]; // second key (single layout)
    private final int[] tKind = new int[MAXT];
    private int tCount;
    private boolean multiMode;

    private static final int NVK = 4;
    private static final int[] VK_CODE = {-1, -2, -3, -4};
    private final int[] vkCount = new int[NVK];

    // multi-mode buttons: [code, x, y, w, h]; geometry in permille of the
    // real screen. Overridable through /jr-vkeys ("code,x,y,w,h;...",
    // signed decimals, any other character is a separator).
    private int[][] vbtn;
    private int vbtnN;

    // cached translucent fills for the on-screen pads (per draw slot)
    private final int[][] ovBuf = new int[8][];
    private final int[] ovW = new int[8];
    private final int[] ovH = new int[8];
    private final int[] ovCol = new int[8];
    private final int[] vkS1 = new int[2];
    private final int[] vkS2 = new int[2];

    private void mapKey(int from, int to) {
        int idx = from + KEY_OFF;
        if (idx >= 0 && idx < KEY_SPAN) {
            xlate[idx] = to;
        }
    }

    private void loadKeyMap() {
        // Built-in defaults for the Bold 9930, applied to EVERY game:
        // Enter (10)  -> -6 left soft key (confirm/menu)
        // DEL/Backspace (8) -> -7 right soft key (back/cancel)
        // Escape (27) -> -7 right soft key as well
        // An explicit /jr-keys resource is parsed afterwards and may
        // override these.
        mapKey(10, -6);
        mapKey(8, -7);
        mapKey(27, -7);
        try {
            InputStream is = getClass().getResourceAsStream("/jr-keys");
            if (is == null) {
                return; // no resource -> defaults above only
            }
            StringBuffer sb = new StringBuffer();
            int ch;
            while ((ch = is.read()) > 0) {
                sb.append((char) ch);
            }
            is.close();
            int n = 0;
            int from = 0;
            int val = 0;
            boolean neg = false;
            boolean have = false;
            int len = sb.length();
            for (int i = 0; i <= len; i++) {
                boolean end = i == len;
                char c = end ? ',' : sb.charAt(i);
                if (!end && (c >= '0' && c <= '9')) {
                    val = val * 10 + (c - '0');
                    have = true;
                } else if (!end && c == '-' && !have) {
                    neg = true;
                } else if (c == ',' && have) {
                    int v = neg ? -val : val;
                    if ((n & 1) == 0) {
                        from = v;
                    } else {
                        mapKey(from, v);
                    }
                    n++;
                    val = 0;
                    neg = false;
                    have = false;
                }
            }
        } catch (Exception e) {
            // malformed resource: keep whatever was parsed so far
        }
    }

    private int tr(int keyCode) {
        int idx = keyCode + KEY_OFF;
        if (idx >= 0 && idx < KEY_SPAN && xlate[idx] != 0) {
            return xlate[idx];
        }
        return keyCode;
    }

    protected ScaleCanvas() {
        // false: keep normal key event delivery; key state is synthesised
        // from events in getKeyStates() anyway.
        super(false);
        int w = 240;
        int h = 320;
        try {
            InputStream is = getClass().getResourceAsStream("/jr-size");
            if (is != null) {
                StringBuffer sb = new StringBuffer();
                int ch;
                while ((ch = is.read()) > 0) {
                    sb.append((char) ch);
                }
                is.close();
                String s = sb.toString().trim();
                int comma = s.indexOf(',');
                if (comma > 0) {
                    w = Integer.parseInt(s.substring(0, comma).trim());
                    h = Integer.parseInt(s.substring(comma + 1).trim());
                }
            }
        } catch (Exception e) {
            // keep safe defaults
        }
        nativeW = w;
        nativeH = h;
        buffer = Image.createImage(w, h);
        bg = buffer.getGraphics();
        src = new int[w * h];
        loadKeyMap();
        loadFit();
        loadCrop();
        loadVKeys();
    }

    private void loadCrop() {
        crX = 0;
        crY = 0;
        crW = nativeW;
        crH = nativeH;
        try {
            InputStream is = getClass().getResourceAsStream("/jr-crop");
            if (is == null) {
                return;
            }
            StringBuffer sb = new StringBuffer();
            int ch;
            while ((ch = is.read()) > 0) {
                sb.append((char) ch);
            }
            is.close();
            String s = sb.toString().trim();
            // "x,y,w,h" in native-buffer coordinates, optionally followed
            // by ",1" for per-frame dynamic content-height detection
            // (CLDC has no String.split)
            int[] v = new int[4];
            int start = 0;
            boolean dyn = false;
            for (int i = 0; i < 4; i++) {
                int comma = s.indexOf(',', start);
                if (comma < 0) {
                    if (i != 3) {
                        return; // malformed: keep whole-buffer default
                    }
                    v[i] = Integer.parseInt(s.substring(start).trim());
                    break;
                }
                v[i] = Integer.parseInt(s.substring(start, comma).trim());
                start = comma + 1;
                if (i == 3) {
                    String tail = s.substring(start).trim();
                    dyn = tail.startsWith("1");
                }
            }
            int x = v[0], y = v[1], w = v[2], h = v[3];
            if (w > 0 && h > 0 && x >= 0 && y >= 0
                    && x + w <= nativeW && y + h <= nativeH) {
                crX = x;
                crY = y;
                crW = w;
                crH = h;
                effH = h;
                dynCrop = dyn;
            }
        } catch (Exception e) {
            // keep whole-buffer default
        }
    }

    private void loadFit() {
        fitMode = 0;
        try {
            InputStream is = getClass().getResourceAsStream("/jr-fit");
            if (is == null) {
                return;
            }
            StringBuffer sb = new StringBuffer();
            int ch;
            while ((ch = is.read()) > 0) {
                sb.append((char) ch);
            }
            is.close();
            String s = sb.toString().trim();
            // resource encodes the policy as a single digit:
            // 0=fit 1=stretch 2=cover (text names kept for compatibility)
            if (s.length() > 0) {
                char c = s.charAt(0);
                if (c == '1' || s.equals("stretch")) {
                    fitMode = 1;
                } else if (c == '2' || s.equals("cover")) {
                    fitMode = 2;
                }
            }
        } catch (Exception e) {
            // keep default letterbox
        }
    }

    /** Original game paint(Graphics), renamed by the transformer. */
    protected void paintScaled(Graphics g) {
        // GameCanvas games usually do not override paint() at all; they
        // draw into getGraphics() and call flushGraphics().
    }

    /** Original pointer handlers, renamed by the transformer. */
    protected void scPointerPressed(int x, int y) {
    }

    protected void scPointerReleased(int x, int y) {
    }

    protected void scPointerDragged(int x, int y) {
    }

    /** Original key handlers, renamed by the transformer. */
    protected void scKeyPressed(int keyCode) {
    }

    protected void scKeyReleased(int keyCode) {
    }

    protected void scKeyRepeated(int keyCode) {
    }

    public final void keyPressed(int keyCode) {
        int k = tr(keyCode);
        int bits = keyBits(k);
        keysDown |= bits;
        keysLatched |= bits;
        scKeyPressed(k);
    }

    public final void keyReleased(int keyCode) {
        int k = tr(keyCode);
        keysDown &= ~keyBits(k);
        scKeyReleased(k);
    }

    public final void keyRepeated(int keyCode) {
        scKeyRepeated(tr(keyCode));
    }

    private static int keyBits(int k) {
        switch (k) {
            case -1:
            case '2':
                return UP_PRESSED;
            case -2:
            case '8':
                return DOWN_PRESSED;
            case -3:
            case '4':
                return LEFT_PRESSED;
            case -4:
            case '6':
                return RIGHT_PRESSED;
            case -5:
            case '5':
                return FIRE_PRESSED;
            case '1':
                return UP_PRESSED | LEFT_PRESSED;
            case '3':
                return UP_PRESSED | RIGHT_PRESSED;
            case '7':
                return DOWN_PRESSED | LEFT_PRESSED;
            case '9':
                return DOWN_PRESSED | RIGHT_PRESSED;
            case -8:
                return GAME_A_PRESSED;
            case -9:
                return GAME_B_PRESSED;
            case -10:
                return GAME_C_PRESSED;
            case -11:
                return GAME_D_PRESSED;
            default:
                return 0;
        }
    }

    /**
     * Poll-based GameCanvas games call this; return the latched/current
     * direction state synthesised from key events, clearing the latch.
     */
    public int getKeyStates() {
        int s = keysDown | keysLatched;
        keysLatched = 0;
        return s;
    }

    /**
     * Games that ask getGameAction() now receive the translated T9 digit
     * codes; make the platform recognise them as directional game actions.
     */
    public int getGameAction(int keyCode) {
        switch (keyCode) {
            case '2':
                return UP;
            case '8':
                return DOWN;
            case '4':
                return LEFT;
            case '6':
                return RIGHT;
            case '5':
                return FIRE;
            default:
                // Never delegate to the platform for the digits we inject:
                // RIM maps some of them ('0' etc.) to FIRE/GAME actions of
                // its own, which makes games take the wrong branch (e.g.
                // Contra: space-as-'0' paused instead of jumping). 0 means
                // "no game action" to MIDP games, and also avoids the
                // IllegalArgumentException RIM throws for unknown codes.
                if (keyCode >= '0' && keyCode <= '9') {
                    return 0;
                }
                return super.getGameAction(keyCode);
        }
    }

    public final int getWidth() {
        // Guard the super(false) call: GameCanvas's own constructor may
        // allocate its offscreen buffer via Image.createImage(getWidth(),
        // getHeight()) BEFORE this subclass's fields are initialised.
        return nativeW != 0 ? nativeW : super.getWidth();
    }

    public final int getHeight() {
        return nativeW != 0 ? nativeH : super.getHeight();
    }

    private boolean rotateNeeded(int rw, int rh) {
        return rw < rh && nativeW > nativeH
                && rh / nativeW >= 1 && rw / nativeH >= 1;
    }

    /**
     * Computes the content rectangle for the active fit policy.
     * gCw/gCh = scaled content size, gOx/gOy = its top-left on screen
     * (negative in cover mode). rot=true rotates a landscape game 90
     * degrees, so its base size is nativeH x nativeW.
     */
    private void geom(int rw, int rh, boolean rot) {
        // design content size is the visible crop rectangle; rotated 90
        // degrees its on-screen axes swap; dynamic crop shrinks baseH to
        // the rows actually populated this frame
        int baseW = rot ? crH : crW;
        int baseH = rot ? crW : effH;
        if (fitMode == 1) {
            // stretch: non-uniform fill of the whole screen
            gCw = rw;
            gCh = rh;
        } else {
            // Compare ratios without floats: the width scale <= height
            // scale iff rw/baseW <= rh/baseH iff rw*baseH <= rh*baseW.
            boolean widthLimited = rw * baseH <= rh * baseW;
            if (fitMode == 0) {
                // fit: smaller scale, letterbox the spare axis
                if (widthLimited) {
                    gCw = rw;
                    gCh = rw * baseH / baseW;
                } else {
                    gCh = rh;
                    gCw = rh * baseW / baseH;
                }
            } else {
                // cover: larger scale, crop the overflowing axis
                if (widthLimited) {
                    gCh = rh;
                    gCw = rh * baseW / baseH;
                } else {
                    gCw = rw;
                    gCh = rw * baseH / baseW;
                }
            }
        }
        gOx = (rw - gCw) / 2;
        gOy = (rh - gCh) / 2;
        gRot = rot;
        gRW = rw;
        gRH = rh;
    }

    // ---- Canvas-style delivery path (plain Canvas games) ----
    public final void paint(Graphics g) {
        int rw = super.getWidth();
        int rh = super.getHeight();

        // A persistent offscreen Graphics keeps translate/clip state between
        // frames; the platform hands a fresh Graphics to every paint() call,
        // and games rely on that, so reset the state before each frame.
        bg.translate(-bg.getTranslateX(), -bg.getTranslateY());
        bg.setClip(0, 0, nativeW, nativeH);
        bg.setColor(0, 0, 0);
        bg.fillRect(0, 0, nativeW, nativeH);
        paintScaled(bg);

        composite(g, rw, rh);
    }

    // ---- GameCanvas delivery path ----
    /**
     * The game draws on the native-resolution offscreen buffer instead of
     * the platform surface.
     */
    protected Graphics getGraphics() {
        return bg;
    }

    public void flushGraphics() {
        Graphics g = super.getGraphics();
        composite(g, super.getWidth(), super.getHeight());
        super.flushGraphics();
    }

    public void flushGraphics(int x, int y, int width, int height) {
        flushGraphics();
    }

    /**
     * Finds the last row of the crop rectangle that carries a non-black
     * pixel (sampled every 4th column). The result is rounded UP to a
     * 16-pixel grid so small variations do not cause visible popping
     * between frames. Returns crH when the region is entirely black.
     */
    private int detectEffH() {
        int bottom = crY + crH;
        for (int row = bottom - 1; row >= crY; row--) {
            int base = row * nativeW + crX;
            for (int x = 0; x < crW; x += 4) {
                if ((src[base + x] & 0xFFFFFF) != 0) {
                    int ch = (row + 1 - crY + 15) & ~15;
                    return ch > crH ? crH : ch;
                }
            }
        }
        return crH;
    }

    private void composite(Graphics g, int rw, int rh) {
        g.setClip(0, 0, rw, rh);
        g.setColor(0, 0, 0);
        g.fillRect(0, 0, rw, rh);

        boolean rot = rotateNeeded(rw, rh);
        if (dynCrop && !rot) {
            // one getRGB per frame: detection + all paint paths share it
            buffer.getRGB(src, 0, nativeW, 0, 0, nativeW, nativeH);
            effH = detectEffH();
            srcReady = true;
        } else {
            effH = crH;
            srcReady = false;
        }
        geom(rw, rh, rot);
        if (rot) {
            paintRotated(g, rw, rh);
        } else {
            paintDirect(g, rw, rh);
        }
        srcReady = false;
        drawOverlay(g, rw, rh);
    }

    /**
     * Matching-orientation content. fit draws the smaller content
     * rectangle on the black screen; stretch/cover render the whole
     * screen through screen-indexed lookup tables.
     */
    private void paintDirect(Graphics g, int rw, int rh) {
        if (fitMode == 0) {
            if (crX == 0 && crY == 0 && crW == nativeW
                    && effH == nativeH
                    && gCw == nativeW && gCh == nativeH) {
                g.drawImage(buffer, gOx, gOy, Graphics.LEFT | Graphics.TOP);
                return;
            }
            scaleContent(gCw, gCh);
            g.drawRGB(dst, 0, gCw, gOx, gOy, gCw, gCh, false);
            return;
        }
        if (cvX == null || cvRW != rw || cvRH != rh
                || cvCw != gCw || cvCh != gCh
                || cvOx != gOx || cvOy != gOy || cvEH != effH) {
            cvX = new int[rw];
            cvY = new int[rh];
            for (int x = 0; x < rw; x++) {
                int sx = x - gOx;
                cvX[x] = (sx < 0 || sx >= gCw)
                        ? -1 : crX + sx * crW / gCw;
            }
            for (int y = 0; y < rh; y++) {
                int sy = y - gOy;
                cvY[y] = (sy < 0 || sy >= gCh)
                        ? -1 : crY + sy * effH / gCh;
            }
            cvRW = rw;
            cvRH = rh;
            cvCw = gCw;
            cvCh = gCh;
            cvOx = gOx;
            cvOy = gOy;
            cvEH = effH;
        }
        if (!srcReady) {
            buffer.getRGB(src, 0, nativeW, 0, 0, nativeW, nativeH);
        }
        if (dst == null || dstW != rw || dstH != rh) {
            dst = new int[rw * rh];
            dstW = rw;
            dstH = rh;
        }
        for (int y = 0; y < rh; y++) {
            int row = cvY[y];
            int outRow = y * rw;
            for (int x = 0; x < rw; x++) {
                int col = cvX[x];
                dst[outRow + x] = (row < 0 || col < 0)
                        ? 0xFF000000 : src[row * nativeW + col];
            }
        }
        g.drawRGB(dst, 0, rw, 0, 0, rw, rh, false);
    }

    /** Nearest-neighbour upscale into a content-sized dst (fit mode). */
    private void scaleContent(int sw, int sh) {
        if (!srcReady) {
            buffer.getRGB(src, 0, nativeW, 0, 0, nativeW, nativeH);
        }
        if (dst == null || dstW != sw || dstH != sh) {
            dst = new int[sw * sh];
            dstW = sw;
            dstH = sh;
        }
        if (mapX == null || mapSW != sw || mapSH != sh) {
            mapX = new int[sw];
            mapY = new int[sh];
            for (int x = 0; x < sw; x++) {
                mapX[x] = crX + x * crW / sw;
            }
            for (int y = 0; y < sh; y++) {
                mapY[y] = (crY + y * effH / sh) * nativeW;
            }
            mapSW = sw;
            mapSH = sh;
        }
        for (int y = 0; y < sh; y++) {
            int srcRow = mapY[y];
            int outRow = y * sw;
            for (int x = 0; x < sw; x++) {
                dst[outRow + x] = src[srcRow + mapX[x]];
            }
        }
    }

    /**
     * Portrait screen showing a landscape-native game: rotate the frame 90
     * degrees so it looks correct when the handset is held on its side
     * (device top points to the player's left; the keyboard ends up on the
     * right). On-screen content: cw wide (nativeH), ch tall (nativeW).
     */
    private void paintRotated(Graphics g, int rw, int rh) {
        if (gCw <= 0 || gCh <= 0) {
            return;
        }
        if (fitMode == 0) {
            paintRotatedContent(g, gCw, gCh, gOx, gOy);
            return;
        }
        // stretch/cover: render the full screen; mapRX/mapRY hold the
        // per-screen-index source sampling (-1 where cover crops).
        if (mapRX == null || mapRX.length != rw) {
            mapRX = new int[rw];
        }
        if (mapRY == null || mapRY.length != rh) {
            mapRY = new int[rh];
        }
        for (int x = 0; x < rw; x++) {
            int sx = x - gOx;
            mapRX[x] = (sx < 0 || sx >= gCw)
                    ? -1 : crY + crH - 1 - sx * crH / gCw;
        }
        for (int y = 0; y < rh; y++) {
            int sy = y - gOy;
            mapRY[y] = (sy < 0 || sy >= gCh)
                    ? -1 : crX + sy * crW / gCh;
        }
        buffer.getRGB(src, 0, nativeW, 0, 0, nativeW, nativeH);
        if (dst == null || dstW != rw || dstH != rh) {
            dst = new int[rw * rh];
            dstW = rw;
            dstH = rh;
        }
        for (int y = 0; y < rh; y++) {
            int bx = mapRY[y];
            int outRow = y * rw;
            for (int x = 0; x < rw; x++) {
                int by = mapRX[x];
                dst[outRow + x] = (bx < 0 || by < 0)
                        ? 0xFF000000 : src[bx * nativeH + by];
            }
        }
        g.drawRGB(dst, 0, rw, 0, 0, rw, rh, false);
    }

    /**
     * Rotated render of just the content rectangle (fit mode). Screen
     * local column sx maps to source column nativeH-1-sx, local row sy
     * maps to source row sy.
     */
    private void paintRotatedContent(Graphics g, int cw, int ch,
            int ox, int oy) {
        buffer.getRGB(src, 0, nativeW, 0, 0, nativeW, nativeH);
        if (dst == null || dstW != cw || dstH != ch) {
            dst = new int[cw * ch];
            dstW = cw;
            dstH = ch;
        }
        for (int sy = 0; sy < ch; sy++) {
            int bx = crX + sy * crW / ch;
            int outRow = sy * cw;
            for (int sx = 0; sx < cw; sx++) {
                int by = crY + crH - 1 - sx * crH / cw;
                dst[outRow + sx] = src[bx * nativeH + by];
            }
        }
        g.drawRGB(dst, 0, cw, ox, oy, cw, ch, false);
    }

    /**
     * Maps a physical touch point to game coordinates; returns false for
     * touches outside the letterboxed/rotated content area.
     */
    private boolean mapPointer(int x, int y, int[] out) {
        int rw = super.getWidth();
        int rh = super.getHeight();
        geom(rw, rh, rotateNeeded(rw, rh));
        int sx = x - gOx;
        int sy = y - gOy;
        if (sx < 0 || sy < 0 || sx >= gCw || sy >= gCh) {
            return false;
        }
        if (gRot) {
            out[0] = crX + sy * crW / gCh;
            out[1] = crY + crH - 1 - sx * crH / gCw;
        } else {
            out[0] = crX + sx * crW / gCw;
            out[1] = crY + sy * effH / gCh;
        }
        return true;
    }

    // ---- virtual button layer ----

    private void loadVKeys() {
        // default multi-touch layout (permille of the real screen):
        // bottom-left steer left / steer right, bottom-right gas over brake
        vbtn = new int[8][];
        vbtn[0] = new int[]{-3, 30, 630, 220, 330};
        vbtn[1] = new int[]{-4, 270, 630, 220, 330};
        vbtn[2] = new int[]{-1, 740, 580, 230, 180};
        vbtn[3] = new int[]{-2, 740, 790, 230, 180};
        vbtnN = 4;
        try {
            InputStream is = getClass().getResourceAsStream("/jr-vkeys");
            if (is == null) {
                return;
            }
            StringBuffer sb = new StringBuffer();
            int ch;
            while ((ch = is.read()) > 0) {
                sb.append((char) ch);
            }
            is.close();
            // every 5 signed decimals form a button: code,x,y,w,h
            int[] vals = new int[40];
            int n = 0;
            int val = 0;
            boolean neg = false;
            boolean have = false;
            int len = sb.length();
            for (int i = 0; i <= len && n < vals.length; i++) {
                boolean end = i == len;
                char c = end ? ',' : sb.charAt(i);
                if (!end && c >= '0' && c <= '9') {
                    val = val * 10 + (c - '0');
                    have = true;
                } else if (!end && c == '-' && !have) {
                    neg = true;
                } else if (have) {
                    vals[n++] = neg ? -val : val;
                    val = 0;
                    neg = false;
                    have = false;
                }
            }
            int cnt = n / 5;
            if (cnt > 8) {
                cnt = 8;
            }
            if (cnt > 0) {
                for (int i = 0; i < cnt; i++) {
                    vbtn[i] = new int[]{vals[i * 5], vals[i * 5 + 1],
                            vals[i * 5 + 2], vals[i * 5 + 3], vals[i * 5 + 4]};
                }
                vbtnN = cnt;
            }
        } catch (Exception e) {
            // malformed resource: keep the defaults
        }
    }

    private static int vkIndex(int code) {
        for (int i = 0; i < NVK; i++) {
            if (VK_CODE[i] == code) {
                return i;
            }
        }
        return -1;
    }

    private boolean vkHeld(int code) {
        int i = vkIndex(code);
        return i >= 0 && vkCount[i] > 0;
    }

    /** Key-down synthesiser (refcounted: two touches may share a button). */
    private void vkPress(int code) {
        int i = vkIndex(code);
        if (i < 0) {
            return;
        }
        if (vkCount[i]++ == 0) {
            int bits = keyBits(code);
            keysDown |= bits;
            keysLatched |= bits;
            scKeyPressed(code);
        }
    }

    private void vkRelease(int code) {
        int i = vkIndex(code);
        if (i < 0) {
            return;
        }
        if (vkCount[i] > 0 && --vkCount[i] == 0) {
            keysDown &= ~keyBits(code);
            scKeyReleased(code);
        }
    }

    /** Multi-layout hit test; returns the button's key code or 0. */
    private int hitMulti(int x, int y, int rw, int rh) {
        for (int i = 0; i < vbtnN; i++) {
            int bx = vbtn[i][1] * rw / 1000;
            int by = vbtn[i][2] * rh / 1000;
            int bw = vbtn[i][3] * rw / 1000;
            int bh = vbtn[i][4] * rh / 1000;
            if (x >= bx && x < bx + bw && y >= by && y < by + bh) {
                return vbtn[i][0];
            }
        }
        return 0;
    }

    /**
     * Single-finger fallback: bottom 35% of the screen in vertical
     * thirds. Every zone auto-gasses; the outer thirds steer as well.
     * Fills out[] with 0..2 key codes and returns the count.
     */
    private int hitSingle(int x, int y, int rw, int rh, int[] out) {
        int bandTop = rh - rh * 35 / 100;
        if (y < bandTop || y >= rh) {
            return 0;
        }
        out[0] = -1; // auto throttle
        if (x < rw / 3) {
            out[1] = -3;
            return 2;
        }
        if (x >= rw - rw / 3) {
            out[1] = -4;
            return 2;
        }
        return 1;
    }

    private int nearestTouch(int x, int y) {
        int best = 0;
        int bd = Integer.MAX_VALUE;
        for (int i = 0; i < tCount; i++) {
            int dx = tX[i] - x;
            int dy = tY[i] - y;
            int d = dx * dx + dy * dy;
            if (d < bd) {
                bd = d;
                best = i;
            }
        }
        return best;
    }

    private void removeTouch(int i) {
        tCount--;
        tX[i] = tX[tCount];
        tY[i] = tY[tCount];
        tK1[i] = tK1[tCount];
        tK2[i] = tK2[tCount];
        tKind[i] = tKind[tCount];
    }

    /**
     * Second overlapping press proves real multi-touch: switch to the
     * button layout and re-classify the touches already on screen.
     */
    private void upgradeToMulti(int rw, int rh) {
        multiMode = true;
        for (int i = 0; i < tCount; i++) {
            if (tKind[i] != KIND_KEY) {
                continue;
            }
            if (tK1[i] != 0) {
                vkRelease(tK1[i]);
            }
            if (tK2[i] != 0) {
                vkRelease(tK2[i]);
            }
            tK1[i] = 0;
            tK2[i] = 0;
            int code = hitMulti(tX[i], tY[i], rw, rh);
            if (code != 0) {
                tK1[i] = code;
                vkPress(code);
            } else {
                tKind[i] = KIND_DEAD;
            }
        }
    }

    // ---- on-screen pad rendering (translucent, drawn over the frame) ----

    private void alphaFill(Graphics g, int slot, int x, int y, int w, int h,
            int argb) {
        if (w <= 0 || h <= 0) {
            return;
        }
        int[] buf = ovBuf[slot];
        if (buf == null || ovW[slot] != w || ovH[slot] != h
                || ovCol[slot] != argb) {
            buf = new int[w * h];
            for (int i = 0; i < buf.length; i++) {
                buf[i] = argb;
            }
            ovBuf[slot] = buf;
            ovW[slot] = w;
            ovH[slot] = h;
            ovCol[slot] = argb;
        }
        g.drawRGB(buf, 0, w, x, y, w, h, true);
    }

    private void drawPad(Graphics g, int slot, int x, int y, int w, int h,
            int code, boolean held) {
        alphaFill(g, slot, x, y, w, h, held ? 0x88FFCC66 : 0x553366AA);
        g.setColor(held ? 0xFFFFEE88 : 0xFF99BBDD);
        g.drawRect(x, y, w - 1, h - 1);
        g.drawRect(x + 1, y + 1, w - 3, h - 3);
        int cx = x + w / 2;
        int cy = y + h / 2;
        int s = (w < h ? w : h) / 4;
        if (s < 4) {
            s = 4;
        }
        switch (code) {
            case -1: // gas: up
                g.fillTriangle(cx - s, cy + s, cx + s, cy + s, cx, cy - s);
                break;
            case -2: // brake: down
                g.fillTriangle(cx - s, cy - s, cx + s, cy - s, cx, cy + s);
                break;
            case -3: // steer left
                g.fillTriangle(cx + s, cy - s, cx + s, cy + s, cx - s, cy);
                break;
            case -4: // steer right
                g.fillTriangle(cx - s, cy - s, cx - s, cy + s, cx + s, cy);
                break;
            default:
                break;
        }
    }

    private void drawOverlay(Graphics g, int rw, int rh) {
        g.setClip(0, 0, rw, rh);
        if (multiMode) {
            for (int i = 0; i < vbtnN; i++) {
                int x = vbtn[i][1] * rw / 1000;
                int y = vbtn[i][2] * rh / 1000;
                int w = vbtn[i][3] * rw / 1000;
                int h = vbtn[i][4] * rh / 1000;
                drawPad(g, i, x, y, w, h, vbtn[i][0], vkHeld(vbtn[i][0]));
            }
        } else {
            int bandTop = rh - rh * 35 / 100;
            int bandH = rh - bandTop;
            int third = rw / 3;
            drawPad(g, 0, 0, bandTop, third, bandH, -3, vkHeld(-3));
            drawPad(g, 1, third, bandTop, third, bandH, -1,
                    vkHeld(-1) && !vkHeld(-3) && !vkHeld(-4));
            drawPad(g, 2, rw - third, bandTop, third, bandH, -4, vkHeld(-4));
        }
    }

    public final void pointerPressed(int x, int y) {
        int rw = super.getWidth();
        int rh = super.getHeight();
        if (tCount > 0 && !multiMode) {
            upgradeToMulti(rw, rh);
        }
        if (tCount >= MAXT) {
            return;
        }
        int i = tCount++;
        tX[i] = x;
        tY[i] = y;
        tK1[i] = 0;
        tK2[i] = 0;
        tKind[i] = KIND_DEAD;
        if (multiMode) {
            int code = hitMulti(x, y, rw, rh);
            if (code != 0) {
                tKind[i] = KIND_KEY;
                tK1[i] = code;
                vkPress(code);
                return;
            }
        } else {
            int n = hitSingle(x, y, rw, rh, vkS1);
            if (n > 0) {
                tKind[i] = KIND_KEY;
                tK1[i] = vkS1[0];
                vkPress(tK1[i]);
                if (n > 1) {
                    tK2[i] = vkS1[1];
                    vkPress(tK2[i]);
                }
                return;
            }
        }
        // outside the pads: behave exactly like the old pass-through
        if (mapPointer(x, y, vkS1)) {
            tKind[i] = KIND_GAME;
            scPointerPressed(vkS1[0], vkS1[1]);
        }
    }

    public final void pointerReleased(int x, int y) {
        if (tCount == 0) {
            if (mapPointer(x, y, vkS1)) {
                scPointerReleased(vkS1[0], vkS1[1]);
            }
            return;
        }
        int i = nearestTouch(x, y);
        if (tKind[i] == KIND_KEY) {
            if (tK1[i] != 0) {
                vkRelease(tK1[i]);
            }
            if (tK2[i] != 0) {
                vkRelease(tK2[i]);
            }
        } else if (tKind[i] == KIND_GAME) {
            if (mapPointer(x, y, vkS1)) {
                scPointerReleased(vkS1[0], vkS1[1]);
            }
        }
        removeTouch(i);
    }

    public final void pointerDragged(int x, int y) {
        int rw = super.getWidth();
        int rh = super.getHeight();
        if (tCount == 0) {
            if (mapPointer(x, y, vkS1)) {
                scPointerDragged(vkS1[0], vkS1[1]);
            }
            return;
        }
        int i = nearestTouch(x, y);
        tX[i] = x;
        tY[i] = y;
        if (tKind[i] == KIND_GAME) {
            if (mapPointer(x, y, vkS1)) {
                scPointerDragged(vkS1[0], vkS1[1]);
            }
        } else if (tKind[i] == KIND_KEY) {
            // the finger slid across pads: move the held keys with it
            if (multiMode) {
                int code = hitMulti(x, y, rw, rh);
                if (code != tK1[i]) {
                    if (tK1[i] != 0) {
                        vkRelease(tK1[i]);
                    }
                    tK1[i] = code;
                    if (code != 0) {
                        vkPress(code);
                    }
                }
            } else {
                int n = hitSingle(x, y, rw, rh, vkS2);
                int a = n > 0 ? vkS2[0] : 0;
                int b = n > 1 ? vkS2[1] : 0;
                if (a != tK1[i] || b != tK2[i]) {
                    if (tK1[i] != 0) {
                        vkRelease(tK1[i]);
                    }
                    if (tK2[i] != 0) {
                        vkRelease(tK2[i]);
                    }
                    tK1[i] = a;
                    tK2[i] = b;
                    if (a != 0) {
                        vkPress(a);
                    }
                    if (b != 0) {
                        vkPress(b);
                    }
                }
            }
        }
    }
}
