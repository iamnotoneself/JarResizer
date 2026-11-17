//! 转换主流程：AUTO 注入 ScaleCanvas / FIXED 改尺寸常量 + 兼容层注入。

use std::collections::HashMap;
use std::path::Path;

use crate::archive::{write_jar, Jar};
use crate::consts::*;
use crate::patch::{apply_code_patches, patch_constants, transform_auto, CodePatch};
use crate::scan;
use crate::shim::{apply_shim_edits, scan_shim_needs, PatchSpec, Retarget};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Auto,
    Fixed,
}

#[derive(Clone)]
pub enum KeymapChoice {
    Auto,
    Off,
    Custom(Vec<i32>),
}

pub struct ConvertOptions {
    pub mode: Mode,
    pub native_w: i32,
    pub native_h: i32,
    pub target_w: Option<i32>,
    pub target_h: Option<i32>,
    pub keymap: KeymapChoice,
    pub fit: String,
    pub crop: String,
}

impl Default for ConvertOptions {
    fn default() -> Self {
        ConvertOptions {
            mode: Mode::Auto,
            native_w: 0,
            native_h: 0,
            target_w: None,
            target_h: None,
            keymap: KeymapChoice::Auto,
            fit: String::new(),
            crop: String::new(),
        }
    }
}

fn is_signature_meta(upper: &str) -> bool {
    upper.starts_with("META-INF/")
        && (upper.ends_with(".SF")
            || upper.ends_with(".RSA")
            || upper.ends_with(".DSA")
            || upper.ends_with(".EC"))
}

fn is_replaced_entry(n: &str) -> bool {
    n == "jr/ScaleCanvas.class"
        || n == SIZE_RESOURCE
        || n == KEYS_RESOURCE
        || n == FIT_RESOURCE
        || n == CROP_RESOURCE
}

/// pairs: 扁平 [from,to,...] -> /jr-keys 文本。
pub fn encode_keymap(pairs: &[i32]) -> String {
    pairs
        .iter()
        .map(|v| v.to_string())
        .collect::<Vec<_>>()
        .join(",")
}

pub fn convert(
    jar_in: &str,
    jar_out: &str,
    opts: &ConvertOptions,
    log: &mut dyn FnMut(&str),
) -> Result<u64, String> {
    if !Path::new(jar_in).is_file() {
        return Err(format!("找不到 jar: {jar_in}"));
    }

    // 包完整性：引擎库/残缺包直接拦截，警告仅提示
    let integ = scan::scan_package_integrity(jar_in);
    for w in &integ.warnings {
        log(&format!("包完整性提示: {w}"));
    }
    if !integ.fatal.is_empty() {
        return Err(format!(
            "包完整性检查未通过，该包无法在黑莓上作为应用运行：\n{}\n请换包含游戏本体的完整 jar（通常数百 KB 以上）再转。",
            integ
                .fatal
                .iter()
                .map(|f| format!("  · {f}"))
                .collect::<Vec<_>>()
                .join("\n")
        ));
    }

    let jar = Jar::read(jar_in)?;

    // ---- 原生分辨率
    let (nw, nh) = if opts.native_w > 0 && opts.native_h > 0 {
        (opts.native_w, opts.native_h)
    } else {
        match scan::detect_native_from_assets(&jar) {
            Some((w, h, why)) => {
                log(&format!("原生分辨率: {w}x{h}（自动识别: {why}）"));
                (w, h)
            }
            None => return Err("无法自动识别分辨率，请选“手动”并填写".into()),
        }
    };

    // ---- 按键方案（None=自动识别，Some(空)=关闭）
    let keymap: Option<Vec<i32>> = match &opts.keymap {
        KeymapChoice::Auto => match scan::detect_key_scheme_full(&jar, true).0 {
            Some(s) if s == "vendor" => {
                log("自动键位: 厂商游戏码方案 WASD移动+空格动作，触摸板同向滑动/按压同步可用");
                Some(keymap_vendor())
            }
            Some(_) => {
                log("自动键位: 黑莓键帽数字方案 WER/SDF/ZXC=1-9（E上 S左 X下 F右，D=中央5），空格=5（射击开火/自动开火游戏即跳），触摸板同向可用");
                Some(keymap_digit())
            }
            None => None,
        },
        KeymapChoice::Off => Some(Vec::new()),
        KeymapChoice::Custom(p) => Some(p.clone()),
    };

    scan::scan_vendor_extensions(&jar, log);
    scan::scan_rapc_risks(&jar, log);
    scan::landscape_hint(&jar, nw, nh, log);

    // ---- 兼容层计划
    let shim_plans = scan_shim_needs(&jar);
    let mut pkg_rewrites: Vec<(&str, &str)> = Vec::new();
    let mut method_retargets: Vec<Retarget> = Vec::new();
    let mut code_patches: HashMap<String, Vec<(String, &'static PatchSpec, &'static str)>> =
        HashMap::new();
    let mut key_hook: Option<(&str, &str, &str)> = None;
    for p in &shim_plans {
        if p.arcs.is_empty() {
            continue;
        }
        log(&format!(
            "检测到 {} 引用，将自动注入兼容类: {}",
            p.family.label,
            p.arcs
                .iter()
                .map(|a| a[..a.len() - 6].rsplit('/').next().unwrap_or(a))
                .collect::<Vec<_>>()
                .join(", ")
        ));
        for (cn, mem) in &p.uncovered {
            if let Some(mem) = mem {
                log(&format!(
                    "警告: 兼容层未实现 {}.{}，该功能在真机可能异常",
                    cn.replace('/', "."),
                    mem
                ));
            } else {
                log(&format!(
                    "警告: 兼容层未提供 {}，真机可能 NoClassDefFoundError",
                    cn.replace('/', ".")
                ));
            }
        }
        if p.family.rewrites.len() > 0 {
            pkg_rewrites.extend_from_slice(p.family.rewrites);
            for (a, b) in p.family.rewrites {
                log(&format!("受保护包重定位: {a} -> {b}"));
            }
        }
        for r in p.family.method_retargets {
            method_retargets.push(Retarget {
                class: r.class,
                method: r.method,
                desc: r.desc,
                new_class: r.new_class,
            });
            log(&format!(
                "调用重定向: {}.*{} -> {}.{}",
                r.class, r.method, r.new_class, r.method
            ));
        }
        if let Some(h) = p.family.key_hook {
            key_hook = Some(h);
            log(&format!("画布按键钩子: keyPressed -> {}.{}", h.0, h.1));
        }
        for spec in p.family.code_patches {
            code_patches
                .entry(spec.class.to_string())
                .or_default()
                .push((spec.class.to_string(), spec, p.family.label));
        }
    }

    // ---- Canvas 层级
    let graph = scan::read_class_names(&jar);
    let (transformed, gamecanvas) = scan::canvas_hierarchy(&graph);
    if opts.mode == Mode::Auto && transformed.is_empty() {
        return Err("jar 中未找到任何 lcdui Canvas/GameCanvas 子类，无法适配。".into());
    }
    if !gamecanvas.is_empty() {
        let mut gc: Vec<String> = gamecanvas.iter().cloned().collect();
        gc.sort();
        log(&format!(
            "GameCanvas 游戏: {}（getGraphics/flushGraphics/getKeyStates 由适配层接管）",
            gc.join(", ")
        ));
    }

    let adapter_bytes = if opts.mode == Mode::Auto {
        Some(crate::assets::scale_canvas()?)
    } else {
        None
    };
    let mut counts: HashMap<String, usize> = HashMap::new();
    let mut wrote_class = false;
    let mut out_entries: Vec<(String, Vec<u8>)> = Vec::with_capacity(jar.entries.len() + 16);
    let mut names: std::collections::HashSet<String> = jar.names().into_iter().collect();

    for e in &jar.entries {
        let n = &e.name;
        let upper = n.to_uppercase();
        if is_signature_meta(&upper) {
            log(&format!("删除签名文件: {n}"));
            continue;
        }
        if is_replaced_entry(n) {
            continue;
        }
        let mut payload = e.data.clone();
        if n.ends_with(".class") && (!pkg_rewrites.is_empty() || !method_retargets.is_empty()) {
            payload = apply_shim_edits(&payload, &pkg_rewrites, &method_retargets, None)
                .map_err(|ex| format!("shim 字节码改写 {n} 失败: {ex}"))?;
        }
        let internal = if n.ends_with(".class") {
            Some(scan::class_internal(n))
        } else {
            None
        };
        if let Some(internal) = &internal {
            if transformed.contains(internal) {
                if opts.mode == Mode::Auto {
                    if let Some(out) = transform_auto(&payload, &transformed)
                        .map_err(|ex| format!("改写 {n} 失败: {ex}"))?
                    {
                        payload = out;
                        wrote_class = true;
                    }
                } else {
                    let tw = opts.target_w.unwrap_or(nw);
                    let th = opts.target_h.unwrap_or(nh);
                    let mut reps = HashMap::new();
                    reps.insert(nw, tw);
                    reps.insert(nh, th);
                    let (out, cnt) = patch_constants(&payload, &reps)
                        .map_err(|ex| format!("改写 {n} 失败: {ex}"))?;
                    if let Some(o) = out {
                        payload = o;
                        wrote_class = true;
                        *counts.entry(n.clone()).or_insert(0) += cnt;
                    }
                }
            } else if opts.mode == Mode::Fixed {
                let tw = opts.target_w.unwrap_or(nw);
                let th = opts.target_h.unwrap_or(nh);
                let mut reps = HashMap::new();
                reps.insert(nw, tw);
                reps.insert(nh, th);
                let (out, cnt) = patch_constants(&payload, &reps)
                    .map_err(|ex| format!("改写 {n} 失败: {ex}"))?;
                if let Some(o) = out {
                    payload = o;
                    wrote_class = true;
                    *counts.entry(n.clone()).or_insert(0) += cnt;
                }
            }
        }
        if internal.is_some() {
            if let Some(hook) = key_hook {
                // 最后一步：已挂到 ScaleCanvas 的类不能覆盖 final keyPressed
                payload = apply_shim_edits(&payload, &[], &[], Some(hook))
                    .map_err(|ex| format!("按键钩子注入 {n} 失败: {ex}"))?;
            }
        }
        if let Some(internal) = &internal {
            if let Some(specs) = code_patches.get(internal) {
                let ps: Vec<CodePatch> = specs
                    .iter()
                    .map(|(_, s, label)| CodePatch {
                        method: s.method.to_string(),
                        desc: s.desc.to_string(),
                        off: s.off,
                        expect: s.expect.to_vec(),
                        new: s.new.to_vec(),
                        label: format!("{label} {}", s.label),
                    })
                    .collect();
                payload = apply_code_patches(&payload, internal, &ps, log)
                    .map_err(|ex| format!("字节码补丁 {n} 失败: {ex}"))?;
            }
        }
        out_entries.push((n.clone(), payload));
    }

    if opts.mode == Mode::Auto {
        if let Some(a) = &adapter_bytes {
            out_entries.push((
                format!("{ADAPTER}.class"),
                a.clone(),
            ));
            names.insert(format!("{ADAPTER}.class"));
        }
        out_entries.push((
            SIZE_RESOURCE.to_string(),
            format!("{nw},{nh}").into_bytes(),
        ));
        log(&format!(
            "已注入 jr/ScaleCanvas.class 与 {SIZE_RESOURCE} ({nw}x{nh})"
        ));
        if let Some(pairs) = &keymap {
            if pairs.len() >= 2 && pairs.len() % 2 == 0 {
                out_entries.push((KEYS_RESOURCE.to_string(), encode_keymap(pairs).into_bytes()));
                log(&format!("已注入键位映射 {KEYS_RESOURCE}（{} 组）", pairs.len() / 2));
            } else if !pairs.is_empty() {
                log("警告: 键位映射数量不是偶数对，已忽略");
            }
        }
        match opts.fit.as_str() {
            "fit" | "stretch" | "cover" => {
                let code = match opts.fit.as_str() {
                    "fit" => "0",
                    "stretch" => "1",
                    _ => "2",
                };
                out_entries.push((FIT_RESOURCE.to_string(), code.as_bytes().to_vec()));
                log(&format!("已注入铺满策略 {FIT_RESOURCE}={}", opts.fit));
            }
            _ => {}
        }
        if !opts.crop.is_empty() {
            out_entries.push((CROP_RESOURCE.to_string(), opts.crop.clone().into_bytes()));
            log(&format!("已注入内容区裁剪 {CROP_RESOURCE}={}", opts.crop));
        }
    }

    // ---- 兼容层 class（跳过已存在的同名条目）
    for p in &shim_plans {
        let mut injected = 0;
        for arc in &p.arcs {
            if names.contains(arc) {
                continue;
            }
            let payload = crate::assets::asset_bytes(arc)?;
            out_entries.push((arc.clone(), payload));
            names.insert(arc.clone());
            injected += 1;
        }
        if injected > 0 {
            log(&format!("已注入 {injected} 个 {} 兼容类", p.family.label));
        }
    }

    write_jar(jar_out, &out_entries)?;

    // 游戏原包 bug 的字节码兜底（结构性 no-op）
    crate::tools::run_game_bytecode_patches(jar_out, log);

    if !wrote_class && opts.mode == Mode::Fixed {
        return Err(format!(
            "未在字节码中找到原生尺寸常量 {nw}/{nh}，请确认原始分辨率。"
        ));
    }

    let size = std::fs::metadata(jar_out).map(|m| m.len()).unwrap_or(0);
    log(&format!("输出: {jar_out} ({size} 字节)"));
    if opts.mode == Mode::Fixed {
        let total: usize = counts.values().sum();
        log(&format!("共替换 {total} 处常量"));
        let mut keys: Vec<&String> = counts.keys().collect();
        keys.sort();
        for k in keys {
            if counts[k] > 0 {
                log(&format!("  {k}: {} 处", counts[k]));
            }
        }
    } else {
        log(&format!("改写 {} 个 Canvas 层级类", transformed.len()));
    }
    Ok(size)
}

/// 更新已有 JAD（去掉空行与旧的 URL/Size 行，写回 CRLF）。
pub fn update_jad(
    jad_in: &str,
    jad_out: &str,
    jar_url: &str,
    jar_size: u64,
) -> Result<(), String> {
    let raw = std::fs::read(jad_in).map_err(|e| format!("读取 JAD 失败: {e}"))?;
    let text = String::from_utf8_lossy(&raw);
    let mut out: Vec<String> = Vec::new();
    for ln in text.replace("\r\n", "\n").split('\n') {
        if ln.trim().is_empty() {
            continue;
        }
        let key = ln.split(':').next().unwrap_or("").trim().to_lowercase();
        if key == "midlet-jar-url" || key == "midlet-jar-size" {
            continue;
        }
        out.push(ln.to_string());
    }
    out.push(format!("MIDlet-Jar-URL: {jar_url}"));
    out.push(format!("MIDlet-Jar-Size: {jar_size}"));
    let body = out.join("\r\n") + "\r\n";
    std::fs::write(jad_out, body.as_bytes()).map_err(|e| format!("写入 JAD 失败: {e}"))?;
    Ok(())
}
