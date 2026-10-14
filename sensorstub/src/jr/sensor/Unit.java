package jr.sensor;

/**
 * Minimal JSR-256 Unit stub (relocated from javax.microedition.sensor
 * because BlackBerry rejects third-party classes in javax.* packages):
 * a named measurement unit symbol.
 */
public class Unit {

    private final String symbol;

    public Unit(String symbol) {
        this.symbol = symbol;
    }

    public String toString() {
        return symbol == null ? "" : symbol;
    }
}
