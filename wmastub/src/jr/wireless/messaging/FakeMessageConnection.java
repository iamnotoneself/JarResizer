package jr.wireless.messaging;

/**
 * Fake SMS connection: Chinese feature-phone games use JSR-120 to send
 * premium billing SMS. Sending silently succeeds (no radio, no charge,
 * no permission prompt); receive() yields nothing.
 */
public class FakeMessageConnection implements MessageConnection {

    private final String address;

    public FakeMessageConnection(String url) {
        String a = null;
        if (url != null) {
            int slash = url.indexOf("//");
            if (slash >= 0 && slash + 2 < url.length()) {
                String tail = url.substring(slash + 2);
                int q = tail.indexOf('?');
                a = q >= 0 ? tail.substring(0, q) : tail;
            }
        }
        this.address = a;
    }

    public Message newMessage(String type) {
        if (BINARY_MESSAGE.equals(type)) {
            return new FakeBinaryMessage(address);
        }
        return new FakeTextMessage(address);
    }

    public Message newMessage(String type, String addr) {
        if (BINARY_MESSAGE.equals(type)) {
            return new FakeBinaryMessage(addr);
        }
        return new FakeTextMessage(addr);
    }

    public void send(Message msg) throws java.io.IOException {
        // silently swallow: no premium SMS is ever sent
    }

    public Message receive() throws java.io.IOException {
        return null;
    }

    public void setMessageListener(MessageListener listener)
            throws java.io.IOException {
        // no inbound messages are ever delivered
    }

    public int numberOfSegments(Message msg) {
        return 1;
    }

    public void close() throws java.io.IOException {
        // nothing to release
    }
}
