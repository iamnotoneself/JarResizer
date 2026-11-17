//! 厂商 / JSR 兼容层（stub）扫描与注入。
//!
//! 每个家族对应一组 `assets/` 下预编译 class；游戏 jar 只要引用了该
//! 家族的 API，就把整组 class 注入并把受保护包（java.* / javax.*）
//! 的引用重定位到私有 `jr.*` 包。

use std::collections::{BTreeSet, HashMap, HashSet};

use crate::archive::Jar;
use crate::classfile::{Attr, ClassFile, CpEntry, Member};
use crate::consts::{CANVAS, GAMECANVAS};

pub struct Retarget {
    pub class: &'static str,
    pub method: &'static str,
    pub desc: &'static str,
    pub new_class: &'static str,
}

pub struct PatchSpec {
    pub class: &'static str,
    pub method: &'static str,
    pub desc: &'static str,
    pub off: usize,
    pub expect: &'static [u8],
    pub new: &'static [u8],
    pub label: &'static str,
}

pub struct Family {
    pub label: &'static str,
    pub prefix: &'static str,
    pub scan_prefix: Option<&'static str>,
    pub rewrites: &'static [(&'static str, &'static str)],
    pub method_retargets: &'static [Retarget],
    pub key_hook: Option<(&'static str, &'static str, &'static str)>,
    pub code_patches: &'static [PatchSpec],
    pub files: &'static [&'static str],
    pub companions: &'static [&'static str],
    pub groups: &'static [&'static [&'static str]],
    pub members: &'static [(&'static str, &'static [&'static str])],
}

pub const NOKIA_UI_PREFIX: &str = "com/nokia/mid/ui/";

const DIRECT_GRAPHICS_FAMILY: &[&str] = &[
    "com/nokia/mid/ui/DirectGraphics",
    "com/nokia/mid/ui/DirectUtils",
    "com/nokia/mid/ui/DirectGraphicsImpl",
];

const NOKIA_MEMBERS: &[(&str, &[&str])] = &[
    (
        "com/nokia/mid/ui/DirectGraphics",
        &["fillPolygon", "drawPixels"],
    ),
    ("com/nokia/mid/ui/DirectUtils", &["getDirectGraphics"]),
    ("com/nokia/mid/ui/FullCanvas", &["<init>"]),
    (
        "com/nokia/mid/ui/DeviceControl",
        &[
            "<init>",
            "setLights",
            "flashLights",
            "startVibra",
            "stopVibra",
        ],
    ),
    (
        "com/nokia/mid/ui/TactileFeedback",
        &["<init>", "directFeedback", "triggerFeedback"],
    ),
];

const SENSOR_MEMBERS: &[(&str, &[&str])] = &[
    (
        "javax/microedition/sensor/Unit",
        &["<init>", "toString"],
    ),
    (
        "javax/microedition/sensor/MeasurementRange",
        &[
            "<init>",
            "getSmallestValue",
            "getLargestValue",
            "getResolution",
        ],
    ),
    (
        "javax/microedition/sensor/ChannelInfo",
        &[
            "getDataType",
            "getScale",
            "getUnit",
            "getName",
            "getAccuracy",
            "getMeasurementRanges",
        ],
    ),
    (
        "javax/microedition/sensor/Data",
        &["getChannelInfo", "getIntValues"],
    ),
    ("javax/microedition/sensor/DataListener", &["dataReceived"]),
    (
        "javax/microedition/sensor/SensorInfo",
        &[
            "getChannelInfos",
            "getConnectionType",
            "getContextType",
            "getDescription",
            "getMaxBufferSize",
            "getModel",
            "getProperty",
            "getPropertyNames",
            "getQuantity",
            "getUrl",
            "isAvailabilityPushSupported",
            "isAvailable",
            "isConditionPushSupported",
        ],
    ),
    (
        "javax/microedition/sensor/SensorConnection",
        &["setDataListener", "removeDataListener", "close"],
    ),
    ("javax/microedition/sensor/SensorManager", &["findSensors"]),
];

const JSEC_MEMBERS: &[(&str, &[&str])] = &[(
    "java/security/MessageDigest",
    &["<init>", "getInstance", "reset", "update", "digest"],
)];

const WMA_MEMBERS: &[(&str, &[&str])] = &[
    (
        "javax/wireless/messaging/Message",
        &["getAddress", "setAddress", "getTimestamp"],
    ),
    (
        "javax/wireless/messaging/TextMessage",
        &["getPayloadText", "setPayloadText"],
    ),
    (
        "javax/wireless/messaging/BinaryMessage",
        &["getPayloadData", "setPayloadData"],
    ),
    (
        "javax/wireless/messaging/MessageListener",
        &["notifyIncomingMessage"],
    ),
    (
        "javax/wireless/messaging/MessageConnection",
        &[
            "newMessage",
            "send",
            "receive",
            "close",
            "setMessageListener",
            "numberOfSegments",
        ],
    ),
];

const FILE_MEMBERS: &[(&str, &[&str])] = &[
    (
        "javax/microedition/io/file/FileConnection",
        &[
            "availableSize",
            "canRead",
            "canWrite",
            "create",
            "delete",
            "directorySize",
            "exists",
            "fileSize",
            "getName",
            "getPath",
            "getURL",
            "isDirectory",
            "isHidden",
            "isOpen",
            "lastModified",
            "list",
            "mkdir",
            "openInputStream",
            "openOutputStream",
            "openDataInputStream",
            "openDataOutputStream",
            "rename",
            "setFileConnection",
            "setHidden",
            "setReadable",
            "setWritable",
            "truncate",
            "totalSize",
            "usedSize",
            "close",
        ],
    ),
    (
        "javax/microedition/io/file/FileSystemRegistry",
        &[
            "roots",
            "openFileConnection",
            "addFileSystemListener",
            "removeFileSystemListener",
        ],
    ),
    (
        "javax/microedition/io/file/FileSystemListener",
        &["rootChanged"],
    ),
    (
        "javax/microedition/io/file/IllegalModeException",
        &["<init>"],
    ),
    (
        "javax/microedition/io/file/ConnectionClosedException",
        &["<init>"],
    ),
];

const M3G_MEMBERS: &[(&str, &[&str])] = &[
    (
        "javax/microedition/m3g/Appearance",
        &[
            "<init>",
            "getCompositingMode",
            "getLayer",
            "getTexture",
            "setCompositingMode",
            "setLayer",
            "setPolygonMode",
            "setTexture",
        ],
    ),
    (
        "javax/microedition/m3g/Background",
        &["<init>", "setDepthClearEnable"],
    ),
    ("javax/microedition/m3g/Camera", &["<init>", "setPerspective"]),
    (
        "javax/microedition/m3g/CompositingMode",
        &[
            "<init>",
            "getBlending",
            "isDepthWriteEnabled",
            "setAlphaWriteEnable",
            "setBlending",
            "setDepthWriteEnable",
        ],
    ),
    (
        "javax/microedition/m3g/Graphics3D",
        &[
            "bindTarget",
            "getInstance",
            "releaseTarget",
            "render",
            "setViewport",
        ],
    ),
    (
        "javax/microedition/m3g/Group",
        &["<init>", "addChild", "removeChild"],
    ),
    ("javax/microedition/m3g/Image2D", &["<init>"]),
    (
        "javax/microedition/m3g/Light",
        &["<init>", "setIntensity", "setMode"],
    ),
    ("javax/microedition/m3g/Loader", &["load"]),
    (
        "javax/microedition/m3g/Mesh",
        &["<init>", "getIndexBuffer", "getSubmeshCount", "getVertexBuffer"],
    ),
    (
        "javax/microedition/m3g/Node",
        &[
            "getScope",
            "isRenderingEnabled",
            "setRenderingEnable",
            "setScope",
        ],
    ),
    (
        "javax/microedition/m3g/PolygonMode",
        &[
            "<init>",
            "setCulling",
            "setPerspectiveCorrectionEnable",
            "setShading",
        ],
    ),
    (
        "javax/microedition/m3g/Sprite3D",
        &["<init>", "setAppearance", "setCrop"],
    ),
    (
        "javax/microedition/m3g/Texture2D",
        &[
            "<init>",
            "getBlending",
            "getImage",
            "setBlending",
            "setFiltering",
            "setWrapping",
        ],
    ),
    (
        "javax/microedition/m3g/Transform",
        &["<init>", "get", "set"],
    ),
    (
        "javax/microedition/m3g/Transformable",
        &["getTransform", "setScale", "setTransform", "setTranslation"],
    ),
    (
        "javax/microedition/m3g/VertexBuffer",
        &["setDefaultColor"],
    ),
    (
        "javax/microedition/m3g/World",
        &["<init>", "setActiveCamera", "setBackground"],
    ),
];

const SENSOR_PATCH: &[PatchSpec] = &[PatchSpec {
    class: "cc/jmap/games/GameLogic",
    method: "startGame",
    desc: "()V",
    off: 3,
    expect: b"\x99\x00\x08\x2a\xb6\x00\x69",
    new: b"\x57\x2a\xb6\x00\x69\xb1\x00",
    label: "跳过体感预备画面直接开局",
}];

pub const SHIM_FAMILIES: &[Family] = &[
    Family {
        label: "Nokia UI API",
        prefix: NOKIA_UI_PREFIX,
        scan_prefix: None,
        rewrites: &[],
        method_retargets: &[],
        key_hook: None,
        code_patches: &[],
        files: &[
            "com/nokia/mid/ui/DirectGraphics.class",
            "com/nokia/mid/ui/DirectGraphicsImpl.class",
            "com/nokia/mid/ui/DirectUtils.class",
            "com/nokia/mid/ui/FullCanvas.class",
            "com/nokia/mid/ui/DeviceControl.class",
            "com/nokia/mid/ui/TactileFeedback.class",
        ],
        companions: &[],
        groups: &[DIRECT_GRAPHICS_FAMILY],
        members: NOKIA_MEMBERS,
    },
    Family {
        label: "JSR-256 传感器 API",
        prefix: "jr/sensor/",
        scan_prefix: Some("javax/microedition/sensor/"),
        rewrites: &[("javax/microedition/sensor/", "jr/sensor/")],
        method_retargets: &[Retarget {
            class: "javax/microedition/io/Connector",
            method: "open",
            desc: "(Ljava/lang/String;)Ljavax/microedition/io/Connection;",
            new_class: "jr/sensor/SensorManager",
        }],
        key_hook: Some(("jr/sensor/SensorManager", "keyPressed", "(I)V")),
        code_patches: SENSOR_PATCH,
        files: &[
            "jr/sensor/Unit.class",
            "jr/sensor/MeasurementRange.class",
            "jr/sensor/ChannelInfo.class",
            "jr/sensor/Data.class",
            "jr/sensor/DataListener.class",
            "jr/sensor/SensorInfo.class",
            "jr/sensor/SensorConnection.class",
            "jr/sensor/SensorManager.class",
            "jr/sensor/FakeChannelInfo.class",
            "jr/sensor/FakeSensorInfo.class",
            "jr/sensor/FakeData.class",
            "jr/sensor/FakeSensorConnection.class",
        ],
        companions: &[
            "jr/sensor/FakeChannelInfo.class",
            "jr/sensor/FakeSensorInfo.class",
            "jr/sensor/FakeData.class",
            "jr/sensor/FakeSensorConnection.class",
        ],
        groups: &[],
        members: SENSOR_MEMBERS,
    },
    Family {
        label: "java.security MD5",
        prefix: "jr/jsec/",
        scan_prefix: Some("java/security/MessageDigest"),
        rewrites: &[("java/security/MessageDigest", "jr/jsec/MessageDigest")],
        method_retargets: &[],
        key_hook: None,
        code_patches: &[],
        files: &[
            "jr/jsec/MessageDigest.class",
            "jr/jsec/MessageDigest$MD5Digest.class",
        ],
        companions: &["jr/jsec/MessageDigest$MD5Digest.class"],
        groups: &[],
        members: JSEC_MEMBERS,
    },
    Family {
        label: "JSR-120 无线短信 API",
        prefix: "jr/wireless/",
        scan_prefix: Some("javax/wireless/messaging/"),
        rewrites: &[(
            "javax/wireless/messaging/",
            "jr/wireless/messaging/",
        )],
        method_retargets: &[Retarget {
            class: "javax/microedition/io/Connector",
            method: "open",
            desc: "(Ljava/lang/String;)Ljavax/microedition/io/Connection;",
            new_class: "jr/wireless/Wma",
        }],
        key_hook: None,
        code_patches: &[],
        files: &[
            "jr/wireless/Wma.class",
            "jr/wireless/messaging/Message.class",
            "jr/wireless/messaging/TextMessage.class",
            "jr/wireless/messaging/BinaryMessage.class",
            "jr/wireless/messaging/MessageListener.class",
            "jr/wireless/messaging/MessageConnection.class",
            "jr/wireless/messaging/FakeTextMessage.class",
            "jr/wireless/messaging/FakeBinaryMessage.class",
            "jr/wireless/messaging/FakeMessageConnection.class",
        ],
        companions: &[
            "jr/wireless/Wma.class",
            "jr/wireless/messaging/Message.class",
            "jr/wireless/messaging/TextMessage.class",
            "jr/wireless/messaging/BinaryMessage.class",
            "jr/wireless/messaging/MessageListener.class",
            "jr/wireless/messaging/MessageConnection.class",
            "jr/wireless/messaging/FakeTextMessage.class",
            "jr/wireless/messaging/FakeBinaryMessage.class",
            "jr/wireless/messaging/FakeMessageConnection.class",
        ],
        groups: &[],
        members: WMA_MEMBERS,
    },
    Family {
        label: "JSR-75 文件连接 API",
        prefix: "jr/file/",
        scan_prefix: Some("javax/microedition/io/file/"),
        rewrites: &[("javax/microedition/io/file/", "jr/file/")],
        method_retargets: &[
            Retarget {
                class: "javax/microedition/io/Connector",
                method: "open",
                desc: "(Ljava/lang/String;)Ljavax/microedition/io/Connection;",
                new_class: "jr/file/FileManager",
            },
            Retarget {
                class: "javax/microedition/io/Connector",
                method: "open",
                desc: "(Ljava/lang/String;I)Ljavax/microedition/io/Connection;",
                new_class: "jr/file/FileManager",
            },
            Retarget {
                class: "javax/microedition/io/Connector",
                method: "open",
                desc: "(Ljava/lang/String;IZ)Ljavax/microedition/io/Connection;",
                new_class: "jr/file/FileManager",
            },
        ],
        key_hook: None,
        code_patches: &[],
        files: &[
            "jr/file/FileConnection.class",
            "jr/file/FileSystemRegistry.class",
            "jr/file/FileSystemListener.class",
            "jr/file/IllegalModeException.class",
            "jr/file/ConnectionClosedException.class",
            "jr/file/FakeFileConnection.class",
            "jr/file/FakeFileConnection$Entry.class",
            "jr/file/FakeFileConnection$MemOutputStream.class",
            "jr/file/FileManager.class",
        ],
        companions: &[
            "jr/file/FakeFileConnection.class",
            "jr/file/FakeFileConnection$Entry.class",
            "jr/file/FakeFileConnection$MemOutputStream.class",
            "jr/file/FileManager.class",
            "jr/file/ConnectionClosedException.class",
            "jr/file/IllegalModeException.class",
        ],
        groups: &[],
        members: FILE_MEMBERS,
    },
    Family {
        label: "JSR-184 M3G",
        prefix: "jr/m3g/",
        scan_prefix: Some("javax/microedition/m3g/"),
        rewrites: &[("javax/microedition/m3g/", "jr/m3g/")],
        method_retargets: &[],
        key_hook: None,
        code_patches: &[],
        files: &[
            "jr/m3g/Appearance.class",
            "jr/m3g/Background.class",
            "jr/m3g/Camera.class",
            "jr/m3g/CompositingMode.class",
            "jr/m3g/Graphics3D.class",
            "jr/m3g/Group.class",
            "jr/m3g/Image2D.class",
            "jr/m3g/IndexBuffer.class",
            "jr/m3g/Light.class",
            "jr/m3g/Loader.class",
            "jr/m3g/Mesh.class",
            "jr/m3g/Node.class",
            "jr/m3g/Object3D.class",
            "jr/m3g/PolygonMode.class",
            "jr/m3g/Sprite3D.class",
            "jr/m3g/Texture2D.class",
            "jr/m3g/Transform.class",
            "jr/m3g/Transformable.class",
            "jr/m3g/TriangleStripArray.class",
            "jr/m3g/VertexBuffer.class",
            "jr/m3g/World.class",
        ],
        companions: &[
            "jr/m3g/Appearance.class",
            "jr/m3g/Background.class",
            "jr/m3g/BR.class",
            "jr/m3g/Camera.class",
            "jr/m3g/CompositingMode.class",
            "jr/m3g/Graphics3D.class",
            "jr/m3g/Group.class",
            "jr/m3g/Image2D.class",
            "jr/m3g/IndexBuffer.class",
            "jr/m3g/Light.class",
            "jr/m3g/Loader.class",
            "jr/m3g/M3GParser.class",
            "jr/m3g/Mesh.class",
            "jr/m3g/Node.class",
            "jr/m3g/Object3D.class",
            "jr/m3g/PolygonMode.class",
            "jr/m3g/Raster$State.class",
            "jr/m3g/Raster.class",
            "jr/m3g/Renderer.class",
            "jr/m3g/Sprite3D.class",
            "jr/m3g/Texture2D.class",
            "jr/m3g/Transform.class",
            "jr/m3g/Transformable.class",
            "jr/m3g/TriangleStripArray.class",
            "jr/m3g/VAData.class",
            "jr/m3g/VertexBuffer.class",
            "jr/m3g/World.class",
        ],
        groups: &[],
        members: M3G_MEMBERS,
    },
];

pub struct ShimPlan {
    pub family: &'static Family,
    pub arcs: BTreeSet<String>,
    pub uncovered: Vec<(String, Option<String>)>,
}

fn scan_prefix(f: &Family) -> &'static str {
    f.scan_prefix.unwrap_or(f.prefix)
}

fn apply_pairs(s: &str, pairs: &[(&str, &str)]) -> String {
    let mut out = s.to_string();
    for (a, b) in pairs {
        out = out.replace(a, b);
    }
    out
}

/// 扫描所有 class 常量池，判断需要注入哪些兼容类、哪些成员未实现。
pub fn scan_shim_needs(jar: &Jar) -> Vec<ShimPlan> {
    let mut refs: Vec<(HashSet<String>, HashMap<String, HashSet<String>>)> = SHIM_FAMILIES
        .iter()
        .map(|_| (HashSet::new(), HashMap::new()))
        .collect();
    let existing: HashSet<String> = jar.names().into_iter().collect();

    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        let cf = match ClassFile::parse(&e.data) {
            Ok(c) => c,
            Err(_) => continue,
        };
        for ent in cf.cp.iter().flatten() {
            match ent {
                CpEntry::Class(ni) => {
                    let cn = match cf.utf8(*ni) {
                        Some(s) => s,
                        None => continue,
                    };
                    for (i, f) in SHIM_FAMILIES.iter().enumerate() {
                        if cn.starts_with(scan_prefix(f)) {
                            refs[i].0.insert(cn.to_string());
                        }
                    }
                }
                CpEntry::Ref { tag, a, b } if *tag == 9 || *tag == 10 || *tag == 11 => {
                    let cn = match cf.class_name(*a) {
                        Some(c) => c,
                        None => continue,
                    };
                    for (i, f) in SHIM_FAMILIES.iter().enumerate() {
                        if cn.starts_with(scan_prefix(f)) {
                            refs[i].0.insert(cn.clone());
                            if let Some(CpEntry::Ref { tag: 12, a: ni, .. }) =
                                cf.cp.get(*b as usize).and_then(|x| x.as_ref())
                            {
                                if let Some(m) = cf.utf8(*ni) {
                                    refs[i]
                                        .1
                                        .entry(cn.clone())
                                        .or_default()
                                        .insert(m.to_string());
                                }
                            }
                        }
                    }
                }
                _ => {}
            }
        }
    }

    let mut results = Vec::new();
    for (i, f) in SHIM_FAMILIES.iter().enumerate() {
        let (referenced, used_members) = &refs[i];
        if referenced.is_empty() {
            continue;
        }
        let inv: Vec<(&str, &str)> = f.rewrites.iter().map(|(a, b)| (*b, *a)).collect();
        let mut provided: HashMap<String, String> = HashMap::new();
        for arc in f.files {
            let old_cn = apply_pairs(&arc[..arc.len() - 6], &inv);
            provided.insert(old_cn, (*arc).to_string());
        }
        let mut needed: HashSet<String> = HashSet::new();
        let mut uncovered: Vec<(String, Option<String>)> = Vec::new();
        let mut sorted_refs: Vec<&String> = referenced.iter().collect();
        sorted_refs.sort();
        for cn in sorted_refs {
            if existing.contains(&format!("{cn}.class")) {
                continue;
            }
            let mut in_group = false;
            for grp in f.groups {
                if grp.contains(&cn.as_str()) {
                    for g in grp.iter() {
                        needed.insert((*g).to_string());
                    }
                    in_group = true;
                    break;
                }
            }
            if in_group {
                continue;
            }
            if provided.contains_key(cn) {
                needed.insert(cn.clone());
            } else {
                uncovered.push((cn.clone(), None));
            }
        }
        for (cn, members) in used_members {
            let covered = f
                .members
                .iter()
                .find(|(c, _)| *c == cn.as_str())
                .map(|(_, m)| *m);
            let covered = match covered {
                Some(c) => c,
                None => continue,
            };
            for m in members {
                if !covered.contains(&m.as_str()) {
                    uncovered.push((cn.clone(), Some(m.clone())));
                }
            }
        }
        let mut arcs: BTreeSet<String> = BTreeSet::new();
        for cn in &needed {
            if let Some(arc) = provided.get(cn) {
                arcs.insert(arc.clone());
            } else {
                uncovered.push((cn.clone(), None));
            }
        }
        if !arcs.is_empty() {
            for arc in f.companions {
                if !existing.contains(*arc) {
                    arcs.insert((*arc).to_string());
                }
            }
        }
        results.push(ShimPlan {
            family: f,
            arcs,
            uncovered,
        });
    }
    results
}

fn code_attr(cf: &mut ClassFile, code: &[u8], max_stack: u16, max_locals: u16) -> Attr {
    let mut blob = Vec::with_capacity(12 + code.len());
    blob.extend_from_slice(&max_stack.to_be_bytes());
    blob.extend_from_slice(&max_locals.to_be_bytes());
    blob.extend_from_slice(&(code.len() as u32).to_be_bytes());
    blob.extend_from_slice(code);
    blob.extend_from_slice(&0u16.to_be_bytes()); // exception_table_length
    blob.extend_from_slice(&0u16.to_be_bytes()); // attributes_count
    Attr {
        name: cf.add_utf8("Code"),
        data: blob,
    }
}

/// 给直接继承 Canvas/GameCanvas 的类补一个
/// `public void keyPressed(int k){ owner.name(k); }`（若不存在）。
pub fn inject_key_hook(cf: &mut ClassFile, hook: (&str, &str, &str)) -> bool {
    let sup = match cf.super_name() {
        Some(s) => s,
        None => return false,
    };
    if sup != CANVAS && sup != GAMECANVAS {
        return false;
    }
    for m in &cf.methods {
        if cf.utf8(m.name) == Some("keyPressed") && cf.utf8(m.desc) == Some("(I)V") {
            return false;
        }
    }
    let (owner, mname, mdesc) = hook;
    let ci = cf.add_class(owner);
    let ni = cf.add_utf8(mname);
    let di = cf.add_utf8(mdesc);
    let mut nati = None;
    for (i, e) in cf.cp.iter().enumerate() {
        if let Some(CpEntry::Ref { tag: 12, a: x, b: y }) = e {
            if *x == ni && *y == di {
                nati = Some(i as u16);
                break;
            }
        }
    }
    let nati = nati.unwrap_or_else(|| {
        cf.cp.push(Some(CpEntry::Ref {
            tag: 12,
            a: ni,
            b: di,
        }));
        (cf.cp.len() - 1) as u16
    });
    cf.cp.push(Some(CpEntry::Ref {
        tag: 10,
        a: ci,
        b: nati,
    }));
    let reference = (cf.cp.len() - 1) as u16;
    // iload_1; invokestatic ref; return —— 直线代码，无需 StackMap
    let mut code = vec![0x1b, 0xb8];
    code.extend_from_slice(&reference.to_be_bytes());
    code.push(0xb1);
    let name_i = cf.add_utf8("keyPressed");
    let desc_i = cf.add_utf8("(I)V");
    let attr = code_attr(cf, &code, 1, 2);
    cf.methods.push(Member {
        access: 0x0001,
        name: name_i,
        desc: desc_i,
        attrs: vec![attr],
    });
    true
}

/// Utf8 包重定位 + Methodref owner 重定向 + 按键钩子注入。
pub fn apply_shim_edits(
    payload: &[u8],
    rewrites: &[(&str, &str)],
    retargets: &[Retarget],
    key_hook: Option<(&str, &str, &str)>,
) -> Result<Vec<u8>, String> {
    if rewrites.is_empty() && retargets.is_empty() && key_hook.is_none() {
        return Ok(payload.to_vec());
    }
    let mut cf = ClassFile::parse(payload)?;
    let mut changed = false;

    if !rewrites.is_empty() {
        for i in 1..cf.cp.len() {
            if let Some(CpEntry::Utf8(s)) = cf.cp[i].clone() {
                let ns = apply_pairs(&s, rewrites);
                if ns != s {
                    cf.cp[i] = Some(CpEntry::Utf8(ns));
                    changed = true;
                }
            }
        }
    }

    if !retargets.is_empty() {
        let mut rule_map: HashMap<(String, String, String), &str> = HashMap::new();
        for r in retargets {
            rule_map.insert(
                (
                    r.class.to_string(),
                    r.method.to_string(),
                    r.desc.to_string(),
                ),
                r.new_class,
            );
        }
        for i in 1..cf.cp.len() {
            let ent = match cf.cp[i].clone() {
                Some(CpEntry::Ref { tag: 10, a, b }) => (a, b),
                _ => continue,
            };
            let cn = match cf.class_name(ent.0) {
                Some(c) => c,
                None => continue,
            };
            let (mn, ds) = match cf.cp.get(ent.1 as usize).cloned().flatten() {
                Some(CpEntry::Ref { tag: 12, a: ni, b: di }) => (cf.utf8s(ni), cf.utf8s(di)),
                _ => continue,
            };
            if let Some(new_cls) = rule_map.get(&(cn, mn, ds)) {
                let ci = cf.add_class(new_cls);
                cf.cp[i] = Some(CpEntry::Ref {
                    tag: 10,
                    a: ci,
                    b: ent.1,
                });
                changed = true;
            }
        }
    }

    if let Some(hook) = key_hook {
        if inject_key_hook(&mut cf, hook) {
            changed = true;
        }
    }
    Ok(if changed { cf.build() } else { payload.to_vec() })
}

/// 兼容旧接口：只做 Utf8 包重定位（等价于 apply_shim_edits 的子集）。
#[allow(dead_code)]
pub fn relocate_packages(payload: &[u8], rewrites: &[(&str, &str)]) -> Result<Vec<u8>, String> {
    apply_shim_edits(payload, rewrites, &[], None)
}

/// 兼容旧接口：只返回 Nokia UI 家族需要的类名与缺口。
#[allow(dead_code)]
pub fn scan_nokia_usage(jar: &Jar) -> (HashSet<String>, Vec<(String, Option<String>)>) {
    for p in scan_shim_needs(jar) {
        if p.family.prefix == NOKIA_UI_PREFIX {
            let names = p
                .arcs
                .iter()
                .map(|a| a[..a.len() - 6].to_string())
                .collect();
            return (names, p.uncovered);
        }
    }
    (HashSet::new(), Vec::new())
}
