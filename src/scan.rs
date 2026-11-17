//! 各类扫描：Canvas 层级、rapc 风险、厂商扩展、原生分辨率推断、
//! 按键方案、包完整性、批量兼容性预扫描。

use std::collections::{HashMap, HashSet, VecDeque};

use crate::archive::Jar;
use crate::bytecode::walk_code;
use crate::classfile::{code_from_blob, ClassFile, CpEntry};
use crate::consts::*;

// ---------------------------------------------------------------- 基础

pub fn read_class_names(jar: &Jar) -> HashMap<String, Option<String>> {
    let mut graph = HashMap::new();
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        if let Ok(cf) = ClassFile::parse(&e.data) {
            if let Some(n) = cf.this_name() {
                graph.insert(n, cf.super_name());
            }
        }
    }
    graph
}

/// 从 jar 文件名推断内部类名（去掉 .class）。Python 用 filename[:-6]。
pub fn class_internal(entry: &str) -> String {
    entry[..entry.len() - 6].to_string()
}

// ------------------------------------------------------ rapc 风险预检

pub fn scan_rapc_risks(jar: &Jar, log: &mut dyn FnMut(&str)) {
    const STACKMAP_LIMIT: usize = 50000;
    const CLASS_CODE_LIMIT: usize = 60000;
    const METHOD_CODE_LIMIT: usize = 20000;
    const TRY_RATIO: f64 = 0.9;
    const TRY_CODE: usize = 3000;
    const COD_SIZE_WARN: u64 = 18 << 20;
    const COD_SIZE_HARD: u64 = 20 << 20;

    let total: u64 = jar.entries.iter().map(|e| e.data.len() as u64).sum();
    if total >= COD_SIZE_WARN {
        let mb = total as f64 / 1048576.0;
        if total >= COD_SIZE_HARD {
            log(&format!(
                "警告: 本游戏解压后总大小 {mb:.1} MB，超过黑莓单个 COD \
                 应用的容量上限（约 20 MB / 255 个兄弟分片，序值 255 \
                 被根模块保留，rapc 会在第 256 片处内部错误中止；\
                 这是黑莓 OS 平台硬限制，任何选项/优化都无法绕过，\
                 请换更小的游戏版本）。"
            ));
        } else {
            log(&format!(
                "提示: 本游戏解压后 {mb:.1} MB，接近黑莓 COD 255 分片\
                 上限（约 20 MB），rapc 可能在生成 COD 时内部错误\
                 中止，届时只能换更小的游戏版本。"
            ));
        }
    }

    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        let cf = match ClassFile::parse(&e.data) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let mut map_bytes = 0usize;
        let mut code_bytes = 0usize;
        let mut max_method = 0usize;
        let mut whole_try: Vec<(usize, f64, String)> = Vec::new();
        for (mi, ai) in cf.code_refs() {
            let blob = &cf.methods[mi].attrs[ai].data;
            let (code, _) = match code_from_blob(blob) {
                Some(c) => c,
                None => continue,
            };
            let cl = code.len();
            code_bytes += cl;
            max_method = max_method.max(cl);
            let mut p = 8 + cl;
            if p + 2 > blob.len() {
                continue;
            }
            let exn = u16::from_be_bytes([blob[p], blob[p + 1]]) as usize;
            p += 2;
            let mut ranges: Vec<(usize, usize)> = Vec::new();
            for i in 0..exn {
                let q = p + 8 * i;
                if q + 4 > blob.len() {
                    break;
                }
                let s = u16::from_be_bytes([blob[q], blob[q + 1]]) as usize;
                let en = u16::from_be_bytes([blob[q + 2], blob[q + 3]]) as usize;
                ranges.push((s, en));
            }
            p += 8 * exn;
            if !ranges.is_empty() && cl >= TRY_CODE {
                let mut covered = vec![0u8; cl];
                for (s, en) in ranges {
                    let en = en.min(cl);
                    for x in s..en {
                        covered[x] = 1;
                    }
                }
                let cnt = covered.iter().filter(|x| **x != 0).count();
                let ratio = cnt as f64 / cl as f64;
                if ratio >= TRY_RATIO {
                    whole_try.push((cl, ratio, cf.utf8s(cf.methods[mi].name)));
                }
            }
            if p + 2 > blob.len() {
                continue;
            }
            let ac = u16::from_be_bytes([blob[p], blob[p + 1]]) as usize;
            p += 2;
            for _ in 0..ac {
                if p + 6 > blob.len() {
                    break;
                }
                let idx = u16::from_be_bytes([blob[p], blob[p + 1]]);
                let al = u32::from_be_bytes([
                    blob[p + 2],
                    blob[p + 3],
                    blob[p + 4],
                    blob[p + 5],
                ]) as usize;
                if matches!(cf.utf8(idx), Some("StackMap") | Some("StackMapTable")) {
                    map_bytes += al;
                }
                p += 6 + al;
            }
        }
        let mut weak: Vec<String> = Vec::new();
        if code_bytes >= CLASS_CODE_LIMIT {
            weak.push(format!("字节码 {code_bytes} 字节"));
        }
        if max_method >= METHOD_CODE_LIMIT {
            weak.push(format!("含 {max_method} 字节的超大方法（分支/栈帧较多）"));
        }
        if !whole_try.is_empty() {
            let worst = whole_try
                .iter()
                .max_by(|a, b| {
                    a.0.cmp(&b.0)
                        .then(a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
                })
                .unwrap();
            weak.push(format!(
                "{} 个方法被全方法 try/catch 包裹（最大 {} 字节，覆盖率 {:.0}%）",
                whole_try.len(),
                worst.0,
                worst.1 * 100.0
            ));
        }
        let cls_name = &e.name[..e.name.len() - 6];
        if map_bytes >= STACKMAP_LIMIT {
            log(&format!(
                "警告: {cls_name} 验证帧 {map_bytes} 字节，rapc 极可能报 \
                 \"control flow verification information too large\" \
                 拒收 COD（此为强信号，优化无法可靠解决，建议换游戏）。"
            ));
        } else if !weak.is_empty() {
            let tail = if code_bytes >= SPLIT_CLASS_CODE_LIMIT {
                "；若仅为代码段超限，工具会自动把大类方法拆到同类的辅助类后继续构建"
            } else {
                ""
            };
            log(&format!(
                "提示: {cls_name} {}。旧版 rapc 对这类混淆较敏感（少数游戏\
                 会报验证信息过大），实际仍可成功构建，将继续尝试{tail}。",
                weak.join("，")
            ));
        }
    }
}

// ----------------------------------------------- 厂商 / JSR 扩展预警

pub struct VendorFound {
    pub vendor: std::collections::BTreeSet<String>,
    pub jsr: std::collections::BTreeSet<String>,
}

pub fn scan_vendor_extensions(jar: &Jar, log: &mut dyn FnMut(&str)) -> VendorFound {
    let mut found = VendorFound {
        vendor: Default::default(),
        jsr: Default::default(),
    };
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        let cf = match ClassFile::parse(&e.data) {
            Ok(c) => c,
            Err(_) => continue,
        };
        for ent in cf.cp.iter().flatten() {
            let cn = match ent {
                CpEntry::Class(ni) => cf.utf8(*ni).map(|s| s.to_string()),
                CpEntry::Ref { tag, a, .. } if *tag == 9 || *tag == 10 || *tag == 11 => {
                    cf.class_name(*a)
                }
                _ => None,
            };
            let cn = match cn {
                Some(c) => c,
                None => continue,
            };
            if cn.is_empty()
                || cn.starts_with('[')
                || cn.starts_with('(')
                || matches!(
                    cn.chars().next(),
                    Some('V') | Some('T') | Some('I') | Some('J') | Some('Z') | Some('B')
                        | Some('C') | Some('D') | Some('F') | Some('S')
                )
            {
                continue;
            }
            if cn.starts_with("java/") || cn == NOKIA_FULLCANVAS {
                continue;
            }
            if JSR_EXTENSION_PREFIXES.iter().any(|p| cn.starts_with(p)) {
                found.jsr.insert(cn);
                continue;
            }
            if VENDOR_PREFIXES.iter().any(|p| cn.starts_with(p)) {
                found.vendor.insert(cn);
                continue;
            }
        }
    }
    if !found.vendor.is_empty() || !found.jsr.is_empty() {
        log("==== 厂商扩展 API 预警 ====");
        if !found.vendor.is_empty() {
            log("发现厂商扩展 API 引用:");
            for cn in found.vendor.iter().take(20) {
                log(&format!("  - {cn}"));
            }
            if found.vendor.len() > 20 {
                log(&format!("  ... 共 {} 个", found.vendor.len()));
            }
        }
        if !found.jsr.is_empty() {
            log("发现 JSR 扩展 API 引用:");
            for cn in found.jsr.iter().take(20) {
                log(&format!("  - {cn}"));
            }
            if found.jsr.len() > 20 {
                log(&format!("  ... 共 {} 个", found.jsr.len()));
            }
        }
        log("这些类 MicroEmulator 不提供，PC 上加载会 NoClassDefFoundError。");
        log("转换本身不受影响，但请在对应厂商真机上验证运行效果。");
        log("============================");
    }
    found
}

// ---------------------------------------------------- Canvas 层级

pub fn canvas_hierarchy(
    graph: &HashMap<String, Option<String>>,
) -> (HashSet<String>, HashSet<String>) {
    fn chain(
        name: &str,
        graph: &HashMap<String, Option<String>>,
        seen: &mut HashSet<String>,
    ) -> Option<&'static str> {
        if seen.contains(name) {
            return None;
        }
        seen.insert(name.to_string());
        if name == CANVAS || name == NOKIA_FULLCANVAS {
            return Some("canvas");
        }
        if name == GAMECANVAS {
            return Some("gamecanvas");
        }
        let sup = graph.get(name)?.as_deref()?;
        chain(sup, graph, seen)
    }
    let mut transformed = HashSet::new();
    let mut gamecanvas = HashSet::new();
    for name in graph.keys() {
        let mut seen = HashSet::new();
        match chain(name, graph, &mut seen) {
            Some("canvas") => {
                transformed.insert(name.clone());
            }
            Some("gamecanvas") => {
                transformed.insert(name.clone());
                gamecanvas.insert(name.clone());
            }
            _ => {}
        }
    }
    (transformed, gamecanvas)
}

// -------------------------------------------- 分辨率常量 / 图片推断

pub fn scan_constants(jar: &Jar) -> HashMap<i32, usize> {
    let mut tally = HashMap::new();
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        let cf = match ClassFile::parse(&e.data) {
            Ok(c) => c,
            Err(_) => continue,
        };
        for (mi, ai) in cf.code_refs() {
            let code = match cf.code_of(mi, ai) {
                Some(c) => c,
                None => continue,
            };
            for ins in walk_code(&code) {
                if ins.op != 0x10 && ins.op != 0x11 {
                    continue;
                }
                if let Some(v) = crate::bytecode::push_value(ins.op, &ins.operand) {
                    if v > 0 {
                        *tally.entry(v).or_insert(0) += 1;
                    }
                }
            }
        }
    }
    tally
}

pub fn detect_native_size(tally: &HashMap<i32, usize>) -> Option<(i32, i32)> {
    let mut best = None;
    let mut bestscore = 0usize;
    for &(w, h) in PRESETS {
        let cw = *tally.get(&w).unwrap_or(&0);
        let ch = *tally.get(&h).unwrap_or(&0);
        if cw > 0 && ch > 0 {
            let score = cw.min(ch) * 10000 + cw + ch;
            if score > bestscore {
                bestscore = score;
                best = Some((w, h));
            }
        }
    }
    best
}

pub fn image_wh(data: &[u8]) -> Option<(i32, i32)> {
    if data.len() >= 24 && &data[..8] == b"\x89PNG\r\n\x1a\n" {
        let w = u32::from_be_bytes([data[16], data[17], data[18], data[19]]) as i32;
        let h = u32::from_be_bytes([data[20], data[21], data[22], data[23]]) as i32;
        return Some((w, h));
    }
    if data.len() >= 2 && &data[..2] == b"\xff\xd8" {
        let mut i = 2usize;
        while i + 9 < data.len() {
            if data[i] != 0xff {
                i += 1;
                continue;
            }
            let m = data[i + 1];
            let ln = u16::from_be_bytes([data[i + 2], data[i + 3]]) as usize;
            if (0xc0..=0xcf).contains(&m) && !matches!(m, 0xc4 | 0xc8 | 0xcc) {
                let h = u16::from_be_bytes([data[i + 5], data[i + 6]]) as i32;
                let w = u16::from_be_bytes([data[i + 7], data[i + 8]]) as i32;
                return Some((w, h));
            }
            i += 2 + ln;
        }
    }
    None
}

pub fn manifest_text(jar: &Jar) -> Option<String> {
    jar.get("META-INF/MANIFEST.MF")
        .map(|b| String::from_utf8_lossy(b).into_owned())
}

pub fn manifest_orientation(jar: &Jar) -> Option<String> {
    let mf = manifest_text(jar)?;
    for line in mf.lines() {
        let (k, v) = match line.split_once(':') {
            Some(x) => x,
            None => continue,
        };
        let k = k.trim().to_lowercase();
        if matches!(
            k.as_str(),
            "nokia-midlet-app-orientation"
                | "midlet-orientation"
                | "lg-midlet-orientation"
                | "msa-midlet-orientation"
        ) {
            let v = v.trim().to_lowercase();
            if v.starts_with("land") {
                return Some("landscape".into());
            }
            if v.starts_with("port") {
                return Some("portrait".into());
            }
        }
    }
    None
}

pub fn manifest_display_size(jar: &Jar) -> Option<(i32, i32)> {
    let mf = manifest_text(jar)?;
    let want = [
        "nokia-midlet-target-display-size",
        "nokia-midlet-original-display-size",
    ];
    let mut vals: HashMap<String, (i32, i32)> = HashMap::new();
    for line in mf.lines() {
        let (k, v) = match line.split_once(':') {
            Some(x) => x,
            None => continue,
        };
        let k = k.trim().to_lowercase();
        if !want.contains(&k.as_str()) {
            continue;
        }
        let parts: Vec<&str> = v
            .trim()
            .split(|c: char| !c.is_ascii_digit())
            .filter(|s| !s.is_empty())
            .collect();
        if parts.len() >= 2 {
            if let (Ok(a), Ok(b)) = (parts[0].parse(), parts[1].parse()) {
                vals.insert(k, (a, b));
            }
        }
    }
    for k in want {
        if let Some(v) = vals.get(k) {
            return Some(*v);
        }
    }
    None
}

/// 无需用户输入推断设计分辨率与方向（图片 → JAD 方向 → 诺基亚尺寸头 → 常量）。
pub fn detect_native_from_assets(jar: &Jar) -> Option<(i32, i32, String)> {
    let mut pics: Vec<(i64, i32, i32)> = Vec::new();
    let orient = manifest_orientation(jar);
    let msize = manifest_display_size(jar);
    for e in &jar.entries {
        let low = e.name.to_lowercase();
        if !(low.ends_with(".png") || low.ends_with(".jpg") || low.ends_with(".jpeg")) {
            continue;
        }
        if let Some((w, h)) = image_wh(&e.data) {
            pics.push((w as i64 * h as i64, w, h));
        }
    }
    pics.sort_by(|a, b| b.cmp(a));
    let preset_pics: Vec<(i64, i32, i32)> = pics
        .iter()
        .copied()
        .filter(|(_, w, h)| PRESETS.contains(&(*w, *h)) && *w >= 240 && *h >= 240)
        .collect();
    if let Some(orient) = orient {
        let want_land = orient == "landscape";
        if let Some((_, w, h)) = preset_pics.iter().find(|(_, w, h)| (*w > *h) == want_land) {
            return Some((
                *w,
                *h,
                format!("背景图尺寸 {w}x{h}（JAD:{orient}）"),
            ));
        }
        if want_land {
            return Some((640, 360, "JAD 方向标记 landscape".into()));
        }
        return Some((360, 640, "JAD 方向标记 portrait".into()));
    }
    if let Some((w, h)) = msize {
        return Some((w, h, format!("诺基亚尺寸头 {w}x{h}")));
    }
    if let Some((_, w, h)) = preset_pics.first() {
        return Some((*w, *h, format!("背景图尺寸 {w}x{h}")));
    }
    if let Some((area, w, h)) = pics.first() {
        if *area >= 200 * 200 {
            return Some((*w, *h, format!("最大图片 {w}x{h}")));
        }
    }
    if let Some((w, h)) = detect_native_size(&scan_constants(jar)) {
        return Some((w, h, format!("字节码常量推断 {w}x{h}")));
    }
    None
}

// ---------------------------------------------------------- 按键方案

fn method_code(cf: &ClassFile, name: &str, desc: &str) -> Option<Vec<u8>> {
    for m in &cf.methods {
        if cf.utf8(m.name) == Some(name) && cf.utf8(m.desc) == Some(desc) {
            for a in &m.attrs {
                if cf.utf8(a.name) == Some("Code") {
                    return code_from_blob(&a.data).map(|(c, _)| c);
                }
            }
        }
    }
    None
}

fn int_callees(cf: &ClassFile, code: &[u8]) -> Vec<(String, String, String)> {
    let mut out = HashSet::new();
    for ins in walk_code(code) {
        if !matches!(ins.op, 0xb6 | 0xb7 | 0xb8 | 0xb9) || ins.operand.len() < 2 {
            continue;
        }
        let idx = u16::from_be_bytes([ins.operand[0], ins.operand[1]]);
        let (owner, name, desc) = match cf.cp.get(idx as usize).cloned().flatten() {
            Some(CpEntry::Ref { tag, a, b }) if tag == 10 || tag == 11 => {
                let owner = match cf.class_name(a) {
                    Some(o) => o,
                    None => continue,
                };
                match cf.cp.get(b as usize).cloned().flatten() {
                    Some(CpEntry::Ref { tag: 12, a: ni, b: di }) => {
                        (owner, cf.utf8s(ni), cf.utf8s(di))
                    }
                    _ => continue,
                }
            }
            _ => continue,
        };
        if desc == "(I)V" || desc == "(I)I" {
            out.insert((owner, name, desc));
        }
    }
    out.into_iter().collect()
}

/// 统计一个 Code 里的厂商码 / ASCII 数字键常量。
fn scan_key_constants(code: &[u8]) -> (usize, usize, HashSet<i32>) {
    let mut neg = 0usize;
    let mut dig = 0usize;
    let mut digset = HashSet::new();
    let t = crate::bytecode::op_extra_table();
    let mut i = 0usize;
    let end = code.len();
    while i < end {
        let op = code[i];
        if op == 0x10 {
            if i + 1 < end {
                let v = code[i + 1] as i8 as i32;
                if (-22..=-1).contains(&v) {
                    neg += 1;
                }
                if (48..=57).contains(&v) {
                    dig += 1;
                    digset.insert(v);
                }
            }
            i += 2;
        } else if op == 0x11 {
            if i + 2 < end {
                let v = i16::from_be_bytes([code[i + 1], code[i + 2]]) as i32;
                if (-22..=-1).contains(&v) {
                    neg += 1;
                }
                if (48..=57).contains(&v) {
                    dig += 1;
                    digset.insert(v);
                }
            }
            i += 3;
        } else if op == 0xaa {
            let pad = (4 - ((i + 1) % 4)) % 4;
            let base = i + 1 + pad;
            if base + 12 > end {
                break;
            }
            let low = i32::from_be_bytes([
                code[base + 4],
                code[base + 5],
                code[base + 6],
                code[base + 7],
            ]);
            let high = i32::from_be_bytes([
                code[base + 8],
                code[base + 9],
                code[base + 10],
                code[base + 11],
            ]);
            if low <= -1 && high >= -1 && low >= -30 {
                neg += 1;
            }
            if low <= 57 && high >= 48 {
                dig += 1;
                for v in low.max(48)..=high.min(57) {
                    digset.insert(v);
                }
            }
            let cnt = (high as i64 - low as i64 + 1).max(0) as usize;
            i = base + 12 + 4 * cnt;
        } else if op == 0xab {
            let pad = (4 - ((i + 1) % 4)) % 4;
            let base = i + 1 + pad;
            if base + 8 > end {
                break;
            }
            let npairs = i32::from_be_bytes([
                code[base + 4],
                code[base + 5],
                code[base + 6],
                code[base + 7],
            ]);
            if npairs < 0 || base + 8 + 8 * npairs as usize > end {
                break;
            }
            for p in 0..npairs as usize {
                let q = base + 8 + 8 * p;
                let v = i32::from_be_bytes([code[q], code[q + 1], code[q + 2], code[q + 3]]);
                if (-22..=-1).contains(&v) {
                    neg += 1;
                }
                if (48..=57).contains(&v) {
                    dig += 1;
                    digset.insert(v);
                }
            }
            i = base + 8 + 8 * npairs as usize;
        } else {
            i += 1 + t[op as usize];
        }
    }
    (neg, dig, digset)
}

/// 判定游戏认识的键码风格；collect_digits 时同时返回出现过的数字键。
pub fn detect_key_scheme(jar: &Jar) -> Option<String> {
    detect_key_scheme_full(jar, false).0
}

pub fn detect_key_scheme_full(
    jar: &Jar,
    collect_digits: bool,
) -> (Option<String>, HashSet<i32>) {
    let mut vendor_hits = 0usize;
    let mut digit_hits = 0usize;
    let mut digit_seen = HashSet::new();
    let mut classes: HashMap<String, ClassFile> = HashMap::new();
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        if let Ok(cf) = ClassFile::parse(&e.data) {
            classes.insert(class_internal(&e.name), cf);
        }
    }
    let mut queue: VecDeque<(String, String, String, usize)> = VecDeque::new();
    let mut class_keys: Vec<&String> = classes.keys().collect();
    class_keys.sort();
    for entry in class_keys {
        if let Some(cf) = classes.get(entry) {
            if method_code(cf, "keyPressed", "(I)V").is_some() {
                queue.push_back((entry.clone(), "keyPressed".into(), "(I)V".into(), 0));
            }
        }
    }
    let mut seen: HashSet<(String, String, String)> = HashSet::new();
    while let Some((entry, name, desc, depth)) = queue.pop_front() {
        if depth > 4 || seen.contains(&(entry.clone(), name.clone(), desc.clone())) {
            continue;
        }
        seen.insert((entry.clone(), name.clone(), desc.clone()));
        let cf = match classes.get(&entry) {
            Some(c) => c,
            None => continue,
        };
        let code = match method_code(cf, &name, &desc) {
            Some(c) => c,
            None => continue,
        };
        let (neg, dig, digset) = scan_key_constants(&code);
        vendor_hits += neg;
        digit_hits += dig;
        if collect_digits {
            digit_seen.extend(digset);
        }
        if depth < 4 {
            for (owner, mname, mdesc) in int_callees(cf, &code) {
                if classes.contains_key(&owner)
                    && !seen.contains(&(owner.clone(), mname.clone(), mdesc.clone()))
                {
                    queue.push_back((owner, mname, mdesc, depth + 1));
                }
            }
        }
    }
    let scheme = if digit_hits > vendor_hits {
        Some("digit".to_string())
    } else if vendor_hits > 0 {
        Some("vendor".to_string())
    } else if digit_hits > 0 {
        Some("digit".to_string())
    } else {
        None
    };
    (scheme, digit_seen)
}

// ------------------------------------------------------ 批量预扫描

#[derive(Clone)]
pub struct ScanRecord {
    pub jar_path: String,
    pub name: String,
    pub error: Option<String>,
    pub max_major: u16,
    pub class_version_ok: bool,
    pub has_vendor: bool,
    pub has_jsr: bool,
    pub has_fullcanvas: bool,
    pub has_gamecanvas: bool,
    pub has_canvas: bool,
    pub canvas_classes: Vec<String>,
    pub gamecanvas_classes: Vec<String>,
    pub vendor_refs: std::collections::BTreeSet<String>,
    pub jsr_refs: std::collections::BTreeSet<String>,
    pub jsr_unsupported_refs: std::collections::BTreeSet<String>,
    pub jsr_supported_refs: std::collections::BTreeSet<String>,
    pub native_size_guess: Option<(i32, i32)>,
    pub bad_classes: Vec<(String, u16)>,
    pub blackberry_ok: bool,
    pub reason: String,
    pub integrity_fatal: Vec<String>,
    pub integrity_warn: Vec<String>,
}

pub fn scan_one(path: &str) -> ScanRecord {
    let name = std::path::Path::new(path)
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.to_string());
    let mut rec = ScanRecord {
        jar_path: path.to_string(),
        name,
        error: None,
        max_major: 0,
        class_version_ok: true,
        has_vendor: false,
        has_jsr: false,
        has_fullcanvas: false,
        has_gamecanvas: false,
        has_canvas: false,
        canvas_classes: Vec::new(),
        gamecanvas_classes: Vec::new(),
        vendor_refs: Default::default(),
        jsr_refs: Default::default(),
        jsr_unsupported_refs: Default::default(),
        jsr_supported_refs: Default::default(),
        native_size_guess: None,
        bad_classes: Vec::new(),
        blackberry_ok: true,
        reason: String::new(),
        integrity_fatal: Vec::new(),
        integrity_warn: Vec::new(),
    };
    let jar = match Jar::read(path) {
        Ok(j) => j,
        Err(e) => {
            rec.error = Some(e.clone());
            rec.blackberry_ok = false;
            rec.reason = format!("扫描异常: {e}");
            return rec;
        }
    };
    let names: Vec<String> = jar.names();
    let sizes: HashMap<String, u64> = jar
        .entries
        .iter()
        .map(|e| (e.name.clone(), e.data.len() as u64))
        .collect();
    let mf_text = manifest_text(&jar).unwrap_or_default();

    let mut classes: Vec<(String, u16, ClassFile)> = Vec::new();
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        if let Ok(cf) = ClassFile::parse(&e.data) {
            let n = cf.this_name().unwrap_or_else(|| e.name.clone());
            let major = cf.major;
            classes.push((n, major, cf));
        }
    }

    let mut max_major = 0u16;
    let mut bad = Vec::new();
    for (name, major, _) in &classes {
        if *major > max_major {
            max_major = *major;
        }
        if *major > 48 {
            bad.push((name.clone(), *major));
        }
    }
    rec.max_major = max_major;
    rec.bad_classes = bad;
    rec.class_version_ok = max_major <= 48;

    let mut vendor_refs = std::collections::BTreeSet::new();
    let mut jsr_unsupported = std::collections::BTreeSet::new();
    let mut jsr_supported = std::collections::BTreeSet::new();
    for (_, _, cf) in &classes {
        for ent in cf.cp.iter().flatten() {
            let cn = match ent {
                CpEntry::Class(ni) => cf.utf8(*ni).map(|s| s.to_string()),
                CpEntry::Ref { tag, a, .. } if *tag == 9 || *tag == 10 || *tag == 11 => {
                    cf.class_name(*a)
                }
                _ => None,
            };
            let cn = match cn {
                Some(c) => c,
                None => continue,
            };
            if cn.is_empty()
                || cn.starts_with('[')
                || cn.starts_with('(')
                || matches!(
                    cn.chars().next(),
                    Some('V') | Some('T') | Some('I') | Some('J') | Some('Z') | Some('B')
                        | Some('C') | Some('D') | Some('F') | Some('S')
                )
            {
                continue;
            }
            if cn.starts_with("java/") || cn == NOKIA_FULLCANVAS {
                continue;
            }
            if JSR_BLACKBERRY_UNSUPPORTED.iter().any(|p| cn.starts_with(p)) {
                jsr_unsupported.insert(cn);
                continue;
            }
            if JSR_BLACKBERRY_SUPPORTED.iter().any(|p| cn.starts_with(p)) {
                jsr_supported.insert(cn);
                continue;
            }
            if VENDOR_PREFIXES.iter().any(|p| cn.starts_with(p)) {
                vendor_refs.insert(cn);
            }
        }
    }
    rec.vendor_refs = vendor_refs;
    rec.jsr_unsupported_refs = jsr_unsupported;
    rec.jsr_supported_refs = jsr_supported;
    let jr: std::collections::BTreeSet<String> = rec
        .jsr_unsupported_refs
        .union(&rec.jsr_supported_refs)
        .cloned()
        .collect();
    rec.jsr_refs = jr;
    rec.has_vendor = !rec.vendor_refs.is_empty();
    rec.has_jsr = !rec.jsr_unsupported_refs.is_empty();

    let graph: HashMap<String, Option<String>> = classes
        .iter()
        .map(|(n, _, cf)| (n.clone(), cf.super_name()))
        .collect();
    let (canvases, gamecanvas) = canvas_hierarchy(&graph);
    let mut canvas_classes: Vec<String> = canvases.iter().cloned().collect();
    canvas_classes.sort();
    let mut gc_classes: Vec<String> = gamecanvas.iter().cloned().collect();
    gc_classes.sort();
    rec.has_canvas = !canvas_classes.is_empty();
    rec.has_gamecanvas = !gc_classes.is_empty();
    rec.canvas_classes = canvas_classes;
    rec.gamecanvas_classes = gc_classes;
    rec.has_fullcanvas = graph.values().flatten().any(|s| s == NOKIA_FULLCANVAS);

    let mut tally: HashMap<i32, usize> = HashMap::new();
    for (_, _, cf) in &classes {
        for (mi, ai) in cf.code_refs() {
            if let Some(code) = cf.code_of(mi, ai) {
                for ins in walk_code(&code) {
                    if ins.op != 0x10 && ins.op != 0x11 {
                        continue;
                    }
                    if let Some(v) = crate::bytecode::push_value(ins.op, &ins.operand) {
                        if v > 0 {
                            *tally.entry(v).or_insert(0) += 1;
                        }
                    }
                }
            }
        }
    }
    rec.native_size_guess = detect_native_size(&tally);

    let all_class_entries: HashSet<String> = names
        .iter()
        .filter(|n| n.ends_with(".class"))
        .map(|n| class_internal(n))
        .collect();
    let integ = evaluate_integrity(&names, &sizes, &mf_text, &graph, &all_class_entries);
    rec.integrity_fatal = integ.fatal;
    rec.integrity_warn = integ.warnings;

    let mut reasons = Vec::new();
    for f in &rec.integrity_fatal {
        reasons.push(format!("包不可运行: {f}"));
    }
    if !rec.class_version_ok {
        reasons.push(format!("类版本 {max_major}（需 ≤48）"));
    }
    if rec.has_vendor {
        reasons.push("厂商扩展 API".into());
    }
    if !rec.jsr_unsupported_refs.is_empty() {
        reasons.push("JSR 扩展 API（黑莓不支持）".into());
    }
    rec.reason = reasons.join("；");
    rec.blackberry_ok = reasons.is_empty();
    rec
}

pub fn batch_scan(
    dir: &str,
    recursive: bool,
    log: &mut dyn FnMut(&str),
    progress: &mut dyn FnMut(usize, usize, &str),
) -> Vec<ScanRecord> {
    let mut jars: Vec<String> = Vec::new();
    collect_jars(std::path::Path::new(dir), recursive, &mut jars);
    jars.sort();
    let total = jars.len();
    log(&format!("批量扫描: 共 {total} 个 jar"));
    let mut results = Vec::with_capacity(total);
    for (i, jar) in jars.iter().enumerate() {
        progress(i, total, jar);
        let rec = scan_one(jar);
        if !rec.blackberry_ok {
            log(&format!("  ✗ {}  ({})", rec.name, rec.reason));
        }
        results.push(rec);
    }
    progress(total, total, "");
    let ok = results.iter().filter(|r| r.blackberry_ok).count();
    log(&format!("扫描完成: {ok}/{total} 可上黑莓 9930"));
    results
}

fn collect_jars(dir: &std::path::Path, recursive: bool, out: &mut Vec<String>) {
    let rd = match std::fs::read_dir(dir) {
        Ok(r) => r,
        Err(_) => return,
    };
    for ent in rd.flatten() {
        let p = ent.path();
        if p.is_dir() {
            if recursive {
                collect_jars(&p, true, out);
            }
            continue;
        }
        if p.extension()
            .map(|e| e.to_string_lossy().to_lowercase() == "jar")
            .unwrap_or(false)
        {
            out.push(p.to_string_lossy().into_owned());
        }
    }
}

// ------------------------------------------------------ 包完整性

pub struct Integrity {
    pub fatal: Vec<String>,
    pub warnings: Vec<String>,
    /// 保留 MIDlet 清单解析结果（与 Python 返回结构一致，供未来 GUI 使用）。
    #[allow(dead_code)]
    pub midlets: Vec<(u32, String, String, String)>,
    #[allow(dead_code)]
    pub midlet_name: String,
    pub runnable: bool,
}

fn read_manifest_headers(mf_text: &str) -> HashMap<String, String> {
    let text = mf_text.replace("\r\n", "\n").replace('\r', "\n");
    let mut folded: Vec<String> = Vec::new();
    for line in text.split('\n') {
        if line.starts_with(' ') && !folded.is_empty() {
            folded.last_mut().unwrap().push_str(&line[1..]);
        } else {
            folded.push(line.to_string());
        }
    }
    let mut headers = HashMap::new();
    for line in folded {
        if let Some((k, v)) = line.split_once(':') {
            headers.insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    headers
}

fn parse_midlet_entries(headers: &HashMap<String, String>) -> Vec<(u32, String, String, String)> {
    let mut out = Vec::new();
    for (k, v) in headers {
        let num = match k.strip_prefix("MIDlet-") {
            Some(n) if !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()) => n,
            _ => continue,
        };
        let n: u32 = match num.parse() {
            Ok(x) => x,
            Err(_) => continue,
        };
        let parts: Vec<&str> = v.split(',').collect();
        out.push((
            n,
            parts.first().map(|s| s.trim().to_string()).unwrap_or_default(),
            parts
                .get(1)
                .map(|s| s.trim().to_string())
                .unwrap_or_default(),
            parts
                .get(2)
                .map(|s| s.trim().to_string())
                .unwrap_or_default(),
        ));
    }
    out.sort();
    out
}

fn extends_chain(
    internal: &str,
    graph: &HashMap<String, Option<String>>,
    entries: &HashSet<String>,
    targets: &[&str],
) -> bool {
    let mut seen = HashSet::new();
    let mut cur = internal.to_string();
    let mut depth = 0;
    while !cur.is_empty() && !seen.contains(&cur) && depth < 24 {
        seen.insert(cur.clone());
        let sup = match graph.get(&cur) {
            Some(Some(s)) => s.clone(),
            _ => return false,
        };
        if targets.contains(&sup.as_str()) {
            return true;
        }
        if !entries.contains(&sup) {
            return false;
        }
        cur = sup;
        depth += 1;
    }
    false
}

pub fn evaluate_integrity(
    names: &[String],
    sizes: &HashMap<String, u64>,
    mf_text: &str,
    graph: &HashMap<String, Option<String>>,
    class_entries: &HashSet<String>,
) -> Integrity {
    let mut fatal: Vec<String> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();
    let headers = if mf_text.is_empty() {
        HashMap::new()
    } else {
        read_manifest_headers(mf_text)
    };
    let mids = parse_midlet_entries(&headers);
    let has_name = |s: &str| names.iter().any(|n| n == s);

    if !mids.is_empty() {
        for (n, disp, icon, cls) in &mids {
            let label = if !disp.is_empty() {
                disp.clone()
            } else if !cls.is_empty() {
                cls.clone()
            } else {
                format!("MIDlet-{n}")
            };
            if cls.is_empty() {
                fatal.push(format!("MIDlet-{n}（{label}）没有指定启动类"));
                continue;
            }
            let internal = cls.replace('.', "/").trim().to_string();
            if !has_name(&format!("{internal}.class")) {
                fatal.push(format!("启动类 {cls} 不在包内（{label}）——文件残缺"));
                continue;
            }
            if let Some(Some(sup)) = graph.get(&internal) {
                if sup != "javax/microedition/midlet/MIDlet"
                    && !class_entries.contains(sup)
                {
                    warnings.push(format!(
                        "启动类 {} 的父类 {} 不在包内，平台缺失时无法启动",
                        cls,
                        sup.replace('/', ".")
                    ));
                }
            }
            if !icon.is_empty() {
                let stripped = icon.trim_start_matches('/');
                if !has_name(icon) && !has_name(stripped) {
                    warnings.push(format!("图标文件 {icon} 缺失（{label}），将显示默认图标"));
                }
            } else {
                warnings.push(format!("{label} 未声明图标，桌面显示默认图标"));
            }
        }
    } else {
        let any_midlet = class_entries.iter().any(|c| {
            matches!(graph.get(c), Some(Some(s)) if s == "javax/microedition/midlet/MIDlet")
        });
        if !any_midlet {
            let mn = headers
                .get("MIDlet-Name")
                .cloned()
                .unwrap_or_else(|| "未知".into());
            fatal.push(format!(
                "没有 MIDlet 启动入口（清单无 MIDlet-<n>，包内也无继承 \
                 MIDlet 的类；MIDlet-Name={mn}）——这是引擎/类库包或残缺文件，\
                 黑莓会装成桌面无图标的模块"
            ));
        }
    }

    let zero: Vec<&String> = names
        .iter()
        .filter(|n| n.ends_with(".class") && sizes.get(*n).copied().unwrap_or(0) == 0)
        .collect();
    if !zero.is_empty() {
        warnings.push(format!(
            "{} 个 class 为 0 字节（如 {}），包可能不完整",
            zero.len(),
            zero[0].rsplit('/').next().unwrap_or(&zero[0])
        ));
    }
    if class_entries.is_empty() {
        warnings.push("包内没有任何 class 文件".into());
    } else {
        let ui_supers = [CANVAS, GAMECANVAS, NOKIA_FULLCANVAS];
        if !class_entries
            .iter()
            .any(|c| extends_chain(c, graph, class_entries, &ui_supers))
        {
            warnings.push("没有 Canvas/GameCanvas 界面类，可能不是可玩游戏".into());
        }
    }

    Integrity {
        runnable: fatal.is_empty(),
        fatal,
        warnings,
        midlets: mids,
        midlet_name: headers.get("MIDlet-Name").cloned().unwrap_or_default(),
    }
}

pub fn scan_package_integrity(jar_path: &str) -> Integrity {
    let jar = match Jar::read(jar_path) {
        Ok(j) => j,
        Err(e) => {
            return Integrity {
                fatal: vec![format!("jar 无法打开（已损坏或下载不完整）: {e}")],
                warnings: Vec::new(),
                midlets: Vec::new(),
                midlet_name: String::new(),
                runnable: false,
            };
        }
    };
    let names = jar.names();
    let sizes: HashMap<String, u64> = jar
        .entries
        .iter()
        .map(|e| (e.name.clone(), e.data.len() as u64))
        .collect();
    let mf_text = manifest_text(&jar).unwrap_or_default();
    let mut fatal = Vec::new();
    if !names.iter().any(|n| n == "META-INF/MANIFEST.MF") {
        fatal.push("缺少 META-INF/MANIFEST.MF，不是标准 MIDlet 包".into());
    }
    let mut graph = HashMap::new();
    let mut entries = HashSet::new();
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        let internal = class_internal(&e.name);
        entries.insert(internal.clone());
        graph.insert(
            internal,
            ClassFile::parse(&e.data).ok().and_then(|c| c.super_name()),
        );
    }
    let mut ev = evaluate_integrity(&names, &sizes, &mf_text, &graph, &entries);
    if !fatal.is_empty() {
        fatal.append(&mut ev.fatal);
        ev.fatal = fatal;
    }
    ev.runnable = ev.fatal.is_empty();
    ev
}

// ------------------------------------------------------ 横屏锚点提示

pub fn landscape_hint(jar: &Jar, native_w: i32, native_h: i32, log: &mut dyn FnMut(&str)) {
    if native_w != native_h || native_w <= 0 {
        return;
    }
    let mut consts: HashSet<i32> = HashSet::new();
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        let cf = match ClassFile::parse(&e.data) {
            Ok(c) => c,
            Err(_) => continue,
        };
        for (mi, ai) in cf.code_refs() {
            if let Some(code) = cf.code_of(mi, ai) {
                for i in 0..code.len().saturating_sub(2) {
                    if code[i] == 17 {
                        consts.insert(i16::from_be_bytes([code[i + 1], code[i + 2]]) as i32);
                    }
                }
            }
        }
    }
    let half_h = native_h / 2;
    let raw: Vec<i32> = consts
        .iter()
        .copied()
        .filter(|c| {
            (240..=1280).contains(c)
                && *c > native_w
                && c & 1 == 0
                && consts.contains(&(c / 2))
        })
        .collect();
    if raw.is_empty() || !consts.contains(&half_h) {
        return;
    }
    let score = |c: i32| -> i32 {
        let mut s = 0;
        let margin2 = c - native_w;
        if margin2 >= 0 && margin2 & 1 == 0 {
            let m = margin2 / 2;
            if consts.contains(&m) {
                s += 1;
            }
            if consts.contains(&(c - m)) {
                s += 1;
            }
        }
        s
    };
    let mut ranked = raw;
    ranked.sort_by_key(|c| (-score(*c), *c));
    let best = ranked[0];
    if score(best) == 0 {
        return;
    }
    log(&format!(
        "提示: 字节码中存在 {}×{} 横屏逻辑的锚点常量（中心 {},{}），",
        best,
        native_h,
        best / 2,
        half_h
    ));
    log(&format!(
        "      若真机画面横向被裁切，请把原生分辨率改为 {}×{} 后重新转换。",
        best, native_h
    ));
}

// --------------------------------------------------- 类版本（内部使用）

#[allow(dead_code)]
pub fn scan_class_versions(jar: &Jar) -> (u16, Vec<(String, u16)>) {
    let mut max_major = 0u16;
    let mut bad = Vec::new();
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        if let Ok(cf) = ClassFile::parse(&e.data) {
            max_major = max_major.max(cf.major);
            if cf.major > 48 {
                bad.push((cf.this_name().unwrap_or_else(|| e.name.clone()), cf.major));
            }
        }
    }
    (max_major, bad)
}
