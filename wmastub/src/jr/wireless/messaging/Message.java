package jr.wireless.messaging;

/**
 * JSR-120 Message (relocated from javax.wireless.messaging).
 * NOTE: getTimestamp() returns java.util.Date here (not the spec's long)
 * to match the hand-rolled XMessage that CN games bundle — on BlackBerry
 * the whole JSR-120 hierarchy is provided by this stub, so the signatures
 * only need to be consistent with the game's own implementations.
 */
public interface Message {

    String getAddress();

    void setAddress(String address);

    java.util.Date getTimestamp();
}
