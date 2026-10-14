package jr.wireless.messaging;

/**
 * JSR-120 MessageConnection (relocated from javax.wireless.messaging).
 * Extends the real GCF Connection so close()/checkcast patterns keep
 * working after bytecode retargeting. Method set matches the real JSR-120
 * interface so game-bundled XConnection implementations resolve cleanly.
 */
public interface MessageConnection extends javax.microedition.io.Connection {

    String TEXT_MESSAGE = "text";
    String BINARY_MESSAGE = "binary";

    Message newMessage(String type);

    Message newMessage(String type, String address);

    void send(Message msg) throws java.io.IOException;

    Message receive() throws java.io.IOException;

    void setMessageListener(MessageListener listener)
            throws java.io.IOException;

    int numberOfSegments(Message msg);
}
