package jr.wireless.messaging;

/** In-memory BinaryMessage used by the fake SMS connection. */
public class FakeBinaryMessage implements BinaryMessage {

    private String address;
    private byte[] payload;

    public FakeBinaryMessage(String address) {
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

    public byte[] getPayloadData() {
        return payload;
    }

    public void setPayloadData(byte[] data) {
        this.payload = data;
    }
}
