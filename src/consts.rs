//! 全局常量与静态数据（与 resizer.py 一一对应）。

pub const CANVAS: &str = "javax/microedition/lcdui/Canvas";
pub const GAMECANVAS: &str = "javax/microedition/lcdui/game/GameCanvas";
pub const ADAPTER: &str = "jr/ScaleCanvas";
pub const SIZE_RESOURCE: &str = "jr-size";
pub const KEYS_RESOURCE: &str = "jr-keys";
pub const FIT_RESOURCE: &str = "jr-fit";
pub const CROP_RESOURCE: &str = "jr-crop";
pub const NOKIA_FULLCANVAS: &str = "com/nokia/mid/ui/FullCanvas";

pub const VENDOR_PREFIXES: &[&str] = &[
    "com/nokia/",
    "com/motorola/",
    "com/samsung/",
    "com/siemens/",
    "com/sonyericsson/",
    "com/lge/",
    "com/sprint/",
    "com/vodafone/",
];

pub const JSR_EXTENSION_PREFIXES: &[&str] = &[
    "javax/microedition/sensor/",
    "javax/microedition/m3g/",
    "javax/microedition/amms/",
    "javax/microedition/pki/",
    "javax/microedition/location/",
    "javax/microedition/payment/",
    "javax/microedition/provisioning/",
    "javax/microedition/global/",
    "javax/microedition/content/",
    "javax/microedition/xlet/",
    "javax/microedition/media/",
    "javax/microedition/io/pim/",
    "javax/microedition/io/file/",
];

pub const JSR_BLACKBERRY_UNSUPPORTED: &[&str] = &[
    "javax/microedition/sensor/",
    "javax/microedition/m3g/",
    "javax/microedition/amms/",
    "javax/microedition/pki/",
    "javax/microedition/payment/",
    "javax/microedition/provisioning/",
    "javax/microedition/global/",
    "javax/microedition/content/",
    "javax/microedition/xlet/",
];

pub const JSR_BLACKBERRY_SUPPORTED: &[&str] = &[
    "javax/microedition/media/",
    "javax/microedition/location/",
    "javax/microedition/io/pim/",
    "javax/microedition/io/file/",
];

pub const PRESETS: &[(i32, i32)] = &[
    (128, 128),
    (128, 160),
    (132, 176),
    (176, 208),
    (176, 220),
    (208, 208),
    (208, 320),
    (240, 240),
    (240, 320),
    (320, 240),
    (240, 400),
    (240, 432),
    (320, 480),
    (352, 416),
    (360, 640),
    (640, 360),
    (480, 360),
    (480, 640),
    (640, 480),
    (480, 320),
    (320, 320),
    (800, 480),
];

pub const RENAME_METHODS: &[(&str, &str, &str)] = &[
    (
        "paint",
        "(Ljavax/microedition/lcdui/Graphics;)V",
        "paintScaled",
    ),
    ("pointerPressed", "(II)V", "scPointerPressed"),
    ("pointerReleased", "(II)V", "scPointerReleased"),
    ("pointerDragged", "(II)V", "scPointerDragged"),
    ("keyPressed", "(I)V", "scKeyPressed"),
    ("keyReleased", "(I)V", "scKeyReleased"),
    ("keyRepeated", "(I)V", "scKeyRepeated"),
    ("getWidth", "()I", "jr$getWidth"),
    ("getHeight", "()I", "jr$getHeight"),
];

/// CP 方法引用重命名规则（getWidth/getHeight 故意不改，虚分派必须
/// 落到 ScaleCanvas 的原生尺寸实现）。
pub const RENAME_REF_RULES: &[(&str, &str, &str)] = &[
    (
        "paint",
        "(Ljavax/microedition/lcdui/Graphics;)V",
        "paintScaled",
    ),
    ("pointerPressed", "(II)V", "scPointerPressed"),
    ("pointerReleased", "(II)V", "scPointerReleased"),
    ("pointerDragged", "(II)V", "scPointerDragged"),
    ("keyPressed", "(I)V", "scKeyPressed"),
    ("keyReleased", "(I)V", "scKeyReleased"),
    ("keyRepeated", "(I)V", "scKeyRepeated"),
];

pub const SUPER_RETARGET: &[(&str, &str)] = &[
    ("pointerPressed", "(II)V"),
    ("pointerReleased", "(II)V"),
    ("pointerDragged", "(II)V"),
    ("keyPressed", "(I)V"),
    ("keyReleased", "(I)V"),
    ("keyRepeated", "(I)V"),
];

pub const SPLIT_CLASS_CODE_LIMIT: usize = 56000;
pub const SPLIT_CLASS_CODE_TARGET: usize = 45000;

pub const SPLIT_RESERVED_METHODS: &[&str] = &[
    "<init>",
    "<clinit>",
    "paintScaled",
    "scKeyPressed",
    "scKeyReleased",
    "scPointerPressed",
    "scPointerReleased",
    "scPointerDragged",
    "hideNotify",
    "showNotify",
    "notifyInfo",
    "playerUpdate",
];

/// 厂商游戏码方案：WASD 移动 + 空格动作（-1..-5），触摸板同向。
pub const KEYMAP_VENDOR: &[i32] = &[
    119, -1, 87, -1, // w/W up
    115, -2, 83, -2, // s/S down
    97, -3, 65, -3, // a/A left
    100, -4, 68, -4, // d/D right
    32, -5, // space fire
    1, -1, 6, -2, 2, -3, 5, -4, -8, -5,
];

const NUMPAD_KEYS: &[i32] = &[
    119, 49, 87, 49, // w/W -> '1'
    101, 50, 69, 50, // e/E -> '2'
    114, 51, 82, 51, // r/R -> '3'
    115, 52, 83, 52, // s/S -> '4'
    100, 53, 68, 53, // d/D -> '5'
    102, 54, 70, 54, // f/F -> '6'
    122, 55, 90, 55, // z/Z -> '7'
    120, 56, 88, 56, // x/X -> '8'
    99, 57, 67, 57, // c/C -> '9'
];

/// 黑莓键帽数字方案：WER/SDF/ZXC = 1-9，空格 = 中央 5。
pub fn keymap_digit() -> Vec<i32> {
    let mut v = vec![
        32, 53, // space -> '5'
        1, 50, // trackpad up -> '2'
        6, 56, // down -> '8'
        2, 52, // left -> '4'
        5, 54, // right -> '6'
        -8, 53, // press -> '5'
    ];
    v.extend_from_slice(NUMPAD_KEYS);
    v
}

pub fn keymap_vendor() -> Vec<i32> {
    KEYMAP_VENDOR.to_vec()
}
