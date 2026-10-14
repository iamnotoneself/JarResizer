package jr.wireless.messaging;

/** JSR-120 BinaryMessage (relocated). */
public interface BinaryMessage extends Message {

    byte[] getPayloadData();

    void setPayloadData(byte[] data);
}
