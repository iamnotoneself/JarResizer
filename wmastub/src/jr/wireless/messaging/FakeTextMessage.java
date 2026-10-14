package jr.wireless.messaging;

/** In-memory TextMessage used by the fake SMS connection. */
public class FakeTextMessage implements TextMessage {

    private String address;
    private String payload;

    public FakeTextMessage(String address) {
        this.address = address;
    }

    public String getAddress() {
        return address;
    }

    public void setAddress(String address) {
        this.address = address;
    }

    public java.util.Date getTimestamp() {
        return null;
    }

    public String getPayloadText() {
        return payload;
    }

    public void setPayloadText(String text) {
        this.payload = text;
    }
}
