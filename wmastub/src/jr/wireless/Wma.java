package jr.wireless;

import jr.wireless.messaging.FakeMessageConnection;

/**
 * Retarget target of javax.microedition.io.Connector.open(String):
 * sms: URLs get the fake billing-SMS connection; every other scheme
 * (http, socket, file ...) is delegated to the real GCF Connector.
 */
public class Wma {

    public static javax.microedition.io.Connection open(String url)
            throws java.io.IOException {
        if (url != null && url.startsWith("sms")) {
            return new FakeMessageConnection(url);
        }
        return javax.microedition.io.Connector.open(url);
    }
}
