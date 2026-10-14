package jr.wireless.messaging;

/** JSR-120 MessageListener (relocated). */
public interface MessageListener {

    void notifyIncomingMessage(MessageConnection conn);
}
