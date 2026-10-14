package jr.wireless.messaging;

/** JSR-120 TextMessage (relocated). */
public interface TextMessage extends Message {

    String getPayloadText();

    void setPayloadText(String text);
}
