//! 纯逻辑单元测试：不启动 GUI、不调用 Java 工具链、不写真实游戏包。

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use crate::archive::{write_jar, Jar, JarEntry};
use crate::classfile::{Attr, ClassFile, CpEntry, Member};
use crate::consts::{ADAPTER, CANVAS, GAMECANVAS};
use crate::convert::encode_keymap;
use crate::patch::{patch_constants, transform_auto};
use crate::scan::{canvas_hierarchy, detect_key_scheme_full};
use crate::shim::scan_shim_needs;

const PAINT_DESC: &str = "(Ljavax/microedition/lcdui/Graphics;)V";

// ------------------------------------------------------------ 构造辅助

fn new_class(name: &str, super_name: &str) -> ClassFile {
    let mut cf = ClassFile {
        minor: 0,
        major: 52,
        cp: vec![None],
        access: 0x0021,
        this_c: 0,
        super_c: 0,
        interfaces: Vec::new(),
        fields: Vec::new(),
        methods: Vec::new(),
        attrs: Vec::new(),
    };
    cf.this_c = cf.add_class(name);
    cf.super_c = cf.add_class(super_name);
    cf
}

fn add_methodref(cf: &mut ClassFile, owner: &str, name: &str, desc: &str) -> u16 {
    let c = cf.add_class(owner);
    let nat = cf.add_nat(name, desc);
    cf.cp.push(Some(CpEntry::Ref {
        tag: 10,
        a: c,
        b: nat,
    }));
    (cf.cp.len() - 1) as u16
}

fn code_attr(cf: &mut ClassFile, code: &[u8], max_stack: u16, max_locals: u16) -> Attr {
    let mut data = Vec::new();
    data.extend_from_slice(&max_stack.to_be_bytes());
    data.extend_from_slice(&max_locals.to_be_bytes());
    data.extend_from_slice(&(code.len() as u32).to_be_bytes());
    data.extend_from_slice(code);
    data.extend_from_slice(&0u16.to_be_bytes()); // exception_table_length
    data.extend_from_slice(&0u16.to_be_bytes()); // attributes_count
    Attr {
        name: cf.add_utf8("Code"),
        data,
    }
}

fn add_method(
    cf: &mut ClassFile,
    access: u16,
    name: &str,
    desc: &str,
    code: &[u8],
) {
    let name_i = cf.add_utf8(name);
    let desc_i = cf.add_utf8(desc);
    let attr = code_attr(cf, code, 4, 4);
    cf.methods.push(Member {
        access,
        name: name_i,
        desc: desc_i,
        attrs: vec![attr],
    });
}

fn invokespecial(code: &mut Vec<u8>, cp_index: u16) {
    let b = cp_index.to_be_bytes();
    code.extend_from_slice(&[0xb7, b[0], b[1]]);
}

fn method_names(cf: &ClassFile) -> Vec<String> {
    cf.methods
        .iter()
        .map(|m| cf.utf8s(m.name))
        .collect::<Vec<_>>()
}

fn methodrefs(cf: &ClassFile) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for e in cf.cp.iter().flatten() {
        if let CpEntry::Ref { tag: 10, a, b } = e {
            let owner = cf.class_name(*a).unwrap_or_default();
            if let Some(CpEntry::Ref { tag: 12, a: ni, b: di }) =
                cf.cp.get(*b as usize).and_then(|x| x.as_ref())
            {
                out.push((owner, cf.utf8s(*ni), cf.utf8s(*di)));
            }
        }
    }
    out
}

// ------------------------------------------------------------ class 常量池回写

#[test]
fn assets_class_roundtrip_is_byte_identical() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("assets");
    let mut files = Vec::new();
    collect(&root, &mut files);
    assert!(files.len() >= 30, "assets 下 class 数量异常: {}", files.len());
    for p in files {
        let data = std::fs::read(&p).unwrap();
        let cf = ClassFile::parse(&data)
            .unwrap_or_else(|e| panic!("解析失败 {}: {e}", p.display()));
        let rebuilt = cf.build();
        assert_eq!(
            rebuilt,
            data,
            "class 回写字节不一致: {}",
            p.display()
        );
    }
}

fn collect(dir: &Path, out: &mut Vec<PathBuf>) {
    let rd = match std::fs::read_dir(dir) {
        Ok(r) => r,
        Err(_) => return,
    };
    for e in rd.flatten() {
        let p = e.path();
        if p.is_dir() {
            collect(&p, out);
        } else if p.extension().map(|x| x == "class").unwrap_or(false) {
            out.push(p);
        }
    }
}

// ------------------------------------------------------------ FIXED 常量补丁

fn patch_sample() -> Vec<u8> {
    let mut cf = new_class("game/Sample", "java/lang/Object");
    // bipush 100, sipush 360, return
    let code = [0x10, 100, 0x11, 0x01, 0x68, 0xb1];
    add_method(&mut cf, 0x0001, "size", "()V", &code);
    cf.build()
}

fn first_code(data: &[u8]) -> Vec<u8> {
    let cf = ClassFile::parse(data).unwrap();
    let (mi, ai) = cf.code_refs()[0];
    cf.code_of(mi, ai).unwrap()
}

#[test]
fn patch_bipush_and_sipush_same_length() {
    let data = patch_sample();
    let map = HashMap::from([(100, 120), (360, 480)]);
    let (out, applied) = patch_constants(&data, &map).unwrap();
    assert_eq!(applied, 2);
    let code = first_code(&out.unwrap());
    assert_eq!(code, vec![0x10, 120, 0x11, 0x01, 0xe0, 0xb1]);
}

#[test]
fn patch_rejects_bipush_overflow() {
    let data = patch_sample();
    let err = patch_constants(&data, &HashMap::from([(100, 200)])).unwrap_err();
    assert!(err.contains("BIPUSH"), "{err}");
}

#[test]
fn patch_rejects_sipush_overflow() {
    let data = patch_sample();
    let err = patch_constants(&data, &HashMap::from([(360, 40000)])).unwrap_err();
    assert!(err.contains("SIPUSH"), "{err}");
}

#[test]
fn patch_no_hit_returns_none() {
    let data = patch_sample();
    let (out, applied) = patch_constants(&data, &HashMap::from([(999, 1)])).unwrap();
    assert_eq!(applied, 0);
    assert!(out.is_none());
}

// ------------------------------------------------------------ AUTO 改写

#[test]
fn transform_auto_rewrites_canvas_subclass() {
    let mut cf = new_class("game/MyCanvas", CANVAS);
    let init_ref = add_methodref(&mut cf, CANVAS, "<init>", "()V");
    add_methodref(&mut cf, "game/MyCanvas", "paint", PAINT_DESC);

    let mut init_code = vec![0x2a];
    invokespecial(&mut init_code, init_ref);
    init_code.push(0xb1);
    add_method(&mut cf, 0x0001, "<init>", "()V", &init_code);
    add_method(&mut cf, 0x0004, "paint", PAINT_DESC, &[0xb1]);
    add_method(&mut cf, 0x0001, "getWidth", "()I", &[0x03, 0xb1]);

    let transformed: HashSet<String> =
        std::iter::once("game/MyCanvas".to_string()).collect();
    let out = transform_auto(&cf.build(), &transformed)
        .unwrap()
        .expect("应参与转换");
    let cf2 = ClassFile::parse(&out).unwrap();

    assert_eq!(cf2.super_name().as_deref(), Some(ADAPTER));
    let names = method_names(&cf2);
    assert!(names.contains(&"paintScaled".to_string()), "{names:?}");
    assert!(names.contains(&"jr$getWidth".to_string()), "{names:?}");
    assert!(!names.contains(&"paint".to_string()), "{names:?}");

    let refs = methodrefs(&cf2);
    assert!(
        refs.contains(&(ADAPTER.into(), "<init>".into(), "()V".into())),
        "构造器应改指适配层: {refs:?}"
    );
    assert!(
        refs.iter()
            .any(|(o, n, d)| o == "game/MyCanvas" && n == "paintScaled" && d == PAINT_DESC),
        "自调用 paint 应重命名: {refs:?}"
    );
    assert!(
        !refs
            .iter()
            .any(|(o, n, d)| o == CANVAS && n == "<init>" && d == "()V"),
        "原 Canvas 构造器不应残留"
    );
}

#[test]
fn transform_auto_nops_gamecanvas_bool_arg() {
    let mut cf = new_class("game/G", GAMECANVAS);
    let init_ref = add_methodref(&mut cf, GAMECANVAS, "<init>", "(Z)V");
    let mut init_code = vec![0x2a, 0x04]; // aload_0, iconst_1
    invokespecial(&mut init_code, init_ref);
    init_code.push(0xb1);
    add_method(&mut cf, 0x0001, "<init>", "()V", &init_code);

    let transformed: HashSet<String> = std::iter::once("game/G".to_string()).collect();
    let out = transform_auto(&cf.build(), &transformed)
        .unwrap()
        .unwrap();
    let cf2 = ClassFile::parse(&out).unwrap();
    assert_eq!(cf2.super_name().as_deref(), Some(ADAPTER));
    let code = first_code(&out);
    assert_eq!(code[1], 0x00, "iconst_1 应被 NOP: {code:?}");
    assert!(
        methodrefs(&cf2).contains(&(ADAPTER.into(), "<init>".into(), "()V".into())),
        "GameCanvas 构造器应改指适配层无参构造"
    );
}

#[test]
fn transform_auto_skips_unrelated_class() {
    let mut cf = new_class("game/Plain", "java/lang/Object");
    add_method(&mut cf, 0x0001, "run", "()V", &[0xb1]);
    let out = transform_auto(&cf.build(), &HashSet::new()).unwrap();
    assert!(out.is_none());
}

// ------------------------------------------------------------ 键位 / 层级 / 兼容层

#[test]
fn encode_keymap_uses_flat_pairs() {
    assert_eq!(encode_keymap(&[119, -1, 32, 53]), "119,-1,32,53");
    assert_eq!(encode_keymap(&[]), "");
}

#[test]
fn keymap_tables_are_well_formed() {
    let digit = crate::consts::keymap_digit();
    let vendor = crate::consts::keymap_vendor();
    assert!(!digit.is_empty() && digit.len() % 2 == 0);
    assert!(!vendor.is_empty() && vendor.len() % 2 == 0);
    assert!(digit.windows(2).any(|w| w == [119, 49]));
    assert!(vendor.windows(2).any(|w| w == [32, -5]));
}

#[test]
fn canvas_hierarchy_follows_super_chain() {
    let mut graph: HashMap<String, Option<String>> = HashMap::new();
    graph.insert("game/A".into(), Some("game/B".into()));
    graph.insert("game/B".into(), Some(CANVAS.into()));
    graph.insert("game/G".into(), Some(GAMECANVAS.into()));
    graph.insert("game/X".into(), Some("java/lang/Object".into()));
    let (transformed, gamecanvas) = canvas_hierarchy(&graph);
    assert!(transformed.contains("game/A"));
    assert!(transformed.contains("game/B"));
    assert!(transformed.contains("game/G"));
    assert!(!transformed.contains("game/X"));
    assert!(gamecanvas.contains("game/G"));
    assert!(!gamecanvas.contains("game/A"));
}

fn jar_of(name: &str, data: Vec<u8>) -> Jar {
    Jar {
        entries: vec![JarEntry {
            name: name.into(),
            data,
        }],
    }
}

#[test]
fn detect_key_scheme_prefers_digits() {
    let mut cf = new_class("game/K", CANVAS);
    // bipush 49, bipush 53, return
    add_method(&mut cf, 0x0001, "keyPressed", "(I)V", &[0x10, 49, 0x10, 53, 0xb1]);
    let (scheme, digits) = detect_key_scheme_full(&jar_of("game/K.class", cf.build()), true);
    assert_eq!(scheme.as_deref(), Some("digit"));
    assert!(digits.contains(&49) && digits.contains(&53));
}

#[test]
fn detect_key_scheme_vendor_codes() {
    let mut cf = new_class("game/V", CANVAS);
    // bipush -5, return
    add_method(&mut cf, 0x0001, "keyPressed", "(I)V", &[0x10, 0xfb, 0xb1]);
    let (scheme, _) = detect_key_scheme_full(&jar_of("game/V.class", cf.build()), false);
    assert_eq!(scheme.as_deref(), Some("vendor"));
}

#[test]
fn shim_scan_detects_nokia_fullcanvas() {
    let mut cf = new_class("game/S", CANVAS);
    cf.add_class("com/nokia/mid/ui/FullCanvas");
    let plans = scan_shim_needs(&jar_of("game/S.class", cf.build()));
    let nokia = plans
        .iter()
        .find(|p| p.family.label == "Nokia UI API")
        .expect("应识别 Nokia UI");
    assert!(nokia
        .arcs
        .contains("com/nokia/mid/ui/FullCanvas.class"));
}

// ------------------------------------------------------------ COD 构建前置：major 48

#[test]
fn change_major_sets_48_and_orders_default_package_first() {
    let dir = std::env::temp_dir().join(format!("jr_test_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let in_jar = dir.join("in.jar");
    let out_jar = dir.join("out.jar");

    let a = new_class("pkg/A", "java/lang/Object").build();
    let d = new_class("d", "java/lang/Object").build();
    write_jar(
        &in_jar.to_string_lossy(),
        &[
            ("pkg/A.class".to_string(), a),
            ("d.class".to_string(), d),
        ],
    )
    .unwrap();

    crate::cod::change_major(&in_jar, &out_jar, crate::cod::CLASS_MAJOR).unwrap();
    let jar = Jar::read(&out_jar.to_string_lossy()).unwrap();
    assert_eq!(jar.entries[0].name, "d.class", "默认包类应排在最前");
    for e in &jar.entries {
        let cf = ClassFile::parse(&e.data).unwrap();
        assert_eq!(cf.major, 48, "{}", e.name);
        assert_eq!(cf.minor, 0);
    }

    let _ = std::fs::remove_dir_all(&dir);
}
