//! BlackBerry COD 一键构建管线：
//! 转换 → ProGuard -microedition 预验证 → class major 48 → JAD → rapc → 装机。

use std::path::{Path, PathBuf};

use crate::archive::{write_jar, Jar};
use crate::convert::{convert, ConvertOptions, KeymapChoice, Mode};
use crate::tools::{device_connected, find_java, root, run_program, run_quiet};

pub const CLASS_MAJOR: u16 = 48;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum LoadMode {
    Auto,
    Never,
    /// 强制重装（GUI 暂未暴露，保留 build_bb.py 的 --force 语义）。
    #[allow(dead_code)]
    Force,
}

pub struct CodOptions {
    pub codname: String,
    pub workdir: Option<String>,
    pub mode: Mode,
    pub keymap: KeymapChoice,
    pub fit: String,
    pub crop: String,
    pub load: LoadMode,
    pub reuse_conv: bool,
}

impl Default for CodOptions {
    fn default() -> Self {
        CodOptions {
            codname: String::new(),
            workdir: None,
            mode: Mode::Auto,
            keymap: KeymapChoice::Auto,
            fit: String::new(),
            crop: String::new(),
            load: LoadMode::Auto,
            reuse_conv: false,
        }
    }
}

struct Toolchain {
    java: PathBuf,
    proguard: PathBuf,
    cldc: PathBuf,
    midp: PathBuf,
    mmapi: PathBuf,
    rapc: PathBuf,
    rim_libs: Vec<PathBuf>,
    javaloader: PathBuf,
}

impl Toolchain {
    fn new() -> Toolchain {
        let r = root();
        Toolchain {
            java: find_java(),
            proguard: r
                .join("tools")
                .join("proguard")
                .join("proguard-7.4.2")
                .join("lib")
                .join("proguard.jar"),
            cldc: r.join("tools").join("cldcapi11.jar"),
            midp: r.join("tools").join("midpapi20.jar"),
            mmapi: r.join("tools").join("mmapi-extra.jar"),
            rapc: r.join("tools").join("rapc.jar"),
            rim_libs: vec![
                r.join("tools").join("net_rim_pdap.cod"),
                r.join("tools").join("net_rim_cldc.cod"),
            ],
            javaloader: r.join("tools").join("javaloader.exe"),
        }
    }
}

fn default_codname(jar_in: &str) -> String {
    let base = Path::new(jar_in)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let n: String = base
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_')
        .collect();
    if n.is_empty() {
        "Game".into()
    } else {
        n
    }
}

/// 把 pv jar 里所有 class 的 major 改为 48；默认包类排在最前，避免
/// rapc 的扁平简单类名表误报 "Duplicate definition for 'd'"。
pub(crate) fn change_major(jar_in: &Path, jar_out: &Path, major: u16) -> Result<(), String> {
    let jar = Jar::read(&jar_in.to_string_lossy())?;
    let mut entries: Vec<(String, Vec<u8>)> = jar
        .entries
        .into_iter()
        .map(|e| (e.name, e.data))
        .collect();
    entries.sort_by(|a, b| {
        let ka = if a.0.ends_with(".class") && !a.0.contains('/') {
            (0u8, a.0.clone())
        } else {
            (1u8, a.0.clone())
        };
        let kb = if b.0.ends_with(".class") && !b.0.contains('/') {
            (0u8, b.0.clone())
        } else {
            (1u8, b.0.clone())
        };
        ka.cmp(&kb)
    });
    for (name, data) in entries.iter_mut() {
        if name.ends_with(".class") && data.len() >= 8 {
            data[4] = 0;
            data[5] = 0;
            let b = major.to_be_bytes();
            data[6] = b[0];
            data[7] = b[1];
        }
    }
    write_jar(&jar_out.to_string_lossy(), &entries)
}

/// MANIFEST → JAD：升级 MIDP2/CLDC1.1，关闭 RIM 触摸兼容模式。
fn gen_jad(jar_path: &Path, jad_path: &Path, log: &mut dyn FnMut(&str)) -> Result<(), String> {
    let jar = Jar::read(&jar_path.to_string_lossy())?;
    let mf = crate::scan::manifest_text(&jar)
        .ok_or_else(|| "jar 内缺少 META-INF/MANIFEST.MF，无法生成 JAD".to_string())?;
    let main = mf.split("\n\n").next().unwrap_or(&mf);
    let mut folded: Vec<String> = Vec::new();
    for ln in main.replace("\r\n", "\n").split('\n') {
        if ln.is_empty() {
            continue;
        }
        if ln.starts_with(' ') && !folded.is_empty() {
            folded.last_mut().unwrap().push_str(&ln[1..]);
        } else {
            folded.push(ln.to_string());
        }
    }
    let mut out: Vec<String> = folded
        .into_iter()
        .filter(|ln| {
            let k = ln
                .split(':')
                .next()
                .unwrap_or("")
                .trim()
                .to_lowercase();
            k != "midlet-jar-url" && k != "midlet-jar-size"
        })
        .collect();
    let set = |out: &mut Vec<String>, key: &str, value: &str| {
        for ln in out.iter_mut() {
            let k = ln
                .split(':')
                .next()
                .unwrap_or("")
                .trim()
                .to_lowercase();
            if k == key.to_lowercase() {
                *ln = format!("{key}: {value}");
                return;
            }
        }
        out.push(format!("{key}: {value}"));
    };
    set(&mut out, "MicroEdition-Profile", "MIDP-2.0");
    set(&mut out, "MicroEdition-Configuration", "CLDC-1.1");
    set(
        &mut out,
        "MIDlet-Jar-URL",
        &jar_path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default(),
    );
    set(
        &mut out,
        "MIDlet-Jar-Size",
        &std::fs::metadata(jar_path)
            .map(|m| m.len())
            .unwrap_or(0)
            .to_string(),
    );
    set(&mut out, "RIM-TouchCompatibilityMode", "false");
    set(&mut out, "RIM-TouchCompatibilityMode-UserChangeable", "false");
    std::fs::write(jad_path, (out.join("\r\n") + "\r\n").as_bytes())
        .map_err(|e| format!("写入 JAD 失败: {e}"))?;
    log(&format!("JAD: {}", jad_path.display()));
    Ok(())
}

/// 增量重建：用 assets/<pkg> 下最新 class 全量刷新 jar 内该包。
fn refresh_stub_assets(jar_path: &Path, pkg: &str, log: &mut dyn FnMut(&str)) -> Result<(), String> {
    let adir = root().join("assets").join(pkg.replace('/', std::path::MAIN_SEPARATOR_STR));
    if !adir.is_dir() {
        return Err(format!("找不到 assets/{pkg}（先运行对应 stub 的 build 脚本）"));
    }
    let mut new: std::collections::HashMap<String, Vec<u8>> = std::collections::HashMap::new();
    for e in std::fs::read_dir(&adir).map_err(|e| e.to_string())?.flatten() {
        let p = e.path();
        if p.extension().map(|x| x == "class").unwrap_or(false) {
            if let Ok(d) = std::fs::read(&p) {
                new.insert(
                    p.file_name().unwrap().to_string_lossy().into_owned(),
                    d,
                );
            }
        }
    }
    if new.is_empty() {
        return Err(format!("assets/{pkg} 下没有 .class（先运行 stub 的 build 脚本）"));
    }
    let mut jar = Jar::read(&jar_path.to_string_lossy())?;
    let prefix = format!("{}/", pkg.trim_end_matches('/'));
    let mut seen = std::collections::HashSet::new();
    let mut kept = 0;
    let mut dropped = 0;
    let mut entries: Vec<(String, Vec<u8>)> = Vec::new();
    for e in jar.entries.drain(..) {
        if e.name.starts_with(&prefix) && e.name.ends_with(".class") {
            let fnm = e.name[prefix.len()..].to_string();
            if let Some(d) = new.get(&fnm) {
                entries.push((e.name, d.clone()));
                seen.insert(fnm);
                kept += 1;
            } else {
                dropped += 1;
            }
            continue;
        }
        entries.push((e.name, e.data));
    }
    let mut added = 0;
    let mut more: Vec<(&String, &Vec<u8>)> = new.iter().filter(|(k, _)| !seen.contains(*k)).collect();
    more.sort_by(|a, b| a.0.cmp(b.0));
    for (k, d) in more {
        entries.push((format!("{prefix}{k}"), d.clone()));
        added += 1;
    }
    write_jar(&jar_path.to_string_lossy(), &entries)?;
    log(&format!("刷新 {pkg}: 替换 {kept}、新增 {added}、剔除 {dropped} 个类"));
    Ok(())
}

fn install(cod: &Path, codname: &str, tc: &Toolchain, workdir: &Path, log: &mut dyn FnMut(&str)) -> Result<(), String> {
    log("---- 装机中 ----");
    let args = vec!["-u".into(), "erase".into(), codname.into()];
    let _ = run_program(&tc.javaloader, &args, workdir, log);
    let args = vec![
        "-u".into(),
        "load".into(),
        cod.to_string_lossy().into_owned(),
    ];
    let (rc, text) = run_program(&tc.javaloader, &args, workdir, log)?;
    if rc != 0 || !text.contains("Done") {
        let tail: String = text.chars().rev().take(500).collect::<Vec<_>>().into_iter().rev().collect();
        return Err(format!(
            "加载失败（请确认手机已连接、游戏已关闭）:\n{tail}"
        ));
    }
    log("装机完成，请在手机上重新进入游戏。");
    Ok(())
}

pub fn build_cod(
    jar_in: &str,
    native_w: i32,
    native_h: i32,
    opts: &CodOptions,
    log: &mut dyn FnMut(&str),
) -> Result<(String, bool), String> {
    if !Path::new(jar_in).is_file() {
        return Err(format!("找不到 jar: {jar_in}"));
    }
    let tc = Toolchain::new();
    for tool in [
        &tc.java,
        &tc.proguard,
        &tc.rapc,
        &tc.cldc,
        &tc.midp,
    ] {
        if !tool.is_file() {
            return Err(format!("缺少工具链文件: {}", tool.display()));
        }
    }
    for lib in &tc.rim_libs {
        if !lib.is_file() {
            return Err(format!("缺少工具链文件: {}", lib.display()));
        }
    }

    let base = Path::new(jar_in)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "game".into());
    let codname = if opts.codname.trim().is_empty() {
        default_codname(jar_in)
    } else {
        opts.codname.trim().to_string()
    };
    let workdir = PathBuf::from(match &opts.workdir {
        Some(w) if !w.trim().is_empty() => w.clone(),
        _ => {
            let mut p = Path::new(jar_in)
                .parent()
                .map(|x| x.to_path_buf())
                .unwrap_or_else(|| PathBuf::from("."));
            p.push("bbbuild");
            p.to_string_lossy().into_owned()
        }
    });
    std::fs::create_dir_all(&workdir).map_err(|e| format!("创建构建目录失败: {e}"))?;

    let conv_jar = workdir.join("game_bb.jar");
    if opts.reuse_conv {
        if !conv_jar.is_file() {
            return Err(format!(
                "增量重建需要已存在的 {}（首次构建请用完整管线）",
                conv_jar.display()
            ));
        }
        log("==== [1/5] 增量重建：复用 game_bb.jar 并刷新 stub ====");
        refresh_stub_assets(&conv_jar, "jr/m3g", log)?;
    } else {
        log(&format!(
            "==== [1/5] 转换 {base} ({native_w}x{native_h}) ===="
        ));
        let mut co = ConvertOptions::default();
        co.mode = opts.mode;
        co.native_w = native_w;
        co.native_h = native_h;
        co.keymap = opts.keymap.clone();
        co.fit = opts.fit.clone();
        co.crop = opts.crop.clone();
        if opts.mode == Mode::Fixed {
            co.keymap = KeymapChoice::Off;
        }
        convert(jar_in, &conv_jar.to_string_lossy(), &co, log)?;
        crate::tools::split_fat_classes(&conv_jar.to_string_lossy(), log);
    }

    // 2) ProGuard 预验证
    let pv_jar = workdir.join("game_pv.jar");
    let pro = workdir.join("preverify.pro");
    let mut cfg = String::new();
    cfg.push_str(&format!(
        "-injars {}\n",
        conv_jar.file_name().unwrap_or_default().to_string_lossy()
    ));
    cfg.push_str(&format!(
        "-outjars {}\n",
        pv_jar.file_name().unwrap_or_default().to_string_lossy()
    ));
    cfg.push_str(&format!("-libraryjars '{}'\n", tc.cldc.display()));
    cfg.push_str(&format!("-libraryjars '{}'\n", tc.midp.display()));
    if tc.mmapi.is_file() {
        cfg.push_str(&format!("-libraryjars '{}'\n", tc.mmapi.display()));
    }
    cfg.push_str("-microedition\n-dontshrink\n-dontoptimize\n-dontobfuscate\n");
    cfg.push_str("-dontnote\n-keep class * { *; }\n-keepattributes *\n");
    std::fs::write(&pro, cfg.as_bytes()).map_err(|e| format!("写入 preverify.pro 失败: {e}"))?;
    log("==== [2/5] ProGuard 预验证 ====");
    if pv_jar.is_file() {
        let _ = std::fs::remove_file(&pv_jar);
    }
    let args = vec![
        "-jar".to_string(),
        tc.proguard.to_string_lossy().into_owned(),
        "@preverify.pro".to_string(),
    ];
    let (rc, text) = run_program(&tc.java, &args, &workdir, log)?;
    if rc != 0 || !pv_jar.is_file() {
        let tail: Vec<&str> = text.lines().rev().take(15).collect();
        return Err(format!(
            "预验证失败（未解析的类/方法引用会导致真机 NoClassDefFoundError，请先补齐兼容层）:\n{}",
            tail.into_iter().rev().collect::<Vec<_>>().join("\n")
        ));
    }

    // 3) class major -> 48
    let u2_jar = workdir.join("game_u2.jar");
    change_major(&pv_jar, &u2_jar, CLASS_MAJOR)?;
    log(&format!("==== [3/5] class 版本 -> major {CLASS_MAJOR} ===="));

    // 4) JAD
    let jad = workdir.join("game_u2.jad");
    gen_jad(&u2_jar, &jad, log)?;

    // 5) rapc
    log(&format!("==== [4/5] rapc 编译 COD ({codname}) ===="));
    let keep: std::collections::HashSet<String> = [
        conv_jar.file_name(),
        pv_jar.file_name(),
        u2_jar.file_name(),
    ]
    .into_iter()
    .flatten()
    .map(|s| s.to_string_lossy().into_owned())
    .collect();
    if let Ok(rd) = std::fs::read_dir(&workdir) {
        for e in rd.flatten() {
            let p = e.path();
            if !p.is_file() {
                continue;
            }
            let fnm = e.file_name().to_string_lossy().into_owned();
            let low = fnm.to_lowercase();
            if low.ends_with(".cso") || low.ends_with(".debug") || low.ends_with(".err") {
                let _ = std::fs::remove_file(&p);
            } else if fnm == format!("{codname}.jar")
                || (low.ends_with(".jar") && !keep.contains(&fnm))
            {
                let _ = std::fs::remove_file(&p);
            } else if fnm.starts_with(&codname) && fnm.ends_with(".cod") {
                let _ = std::fs::remove_file(&p);
            }
        }
    }
    let args = vec![
        "-jar".to_string(),
        tc.rapc.to_string_lossy().into_owned(),
        format!(
            "import={};{}",
            tc.rim_libs[0].display(),
            tc.rim_libs[1].display()
        ),
        format!("codename={codname}"),
        format!(
            "jad={}",
            jad.file_name().unwrap_or_default().to_string_lossy()
        ),
        u2_jar
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned(),
    ];
    let (rc, text) = run_program(&tc.java, &args, &workdir, log)?;
    let cod = workdir.join(format!("{codname}.cod"));
    if rc != 0 || !cod.is_file() {
        if text.contains("control flow verification information") && text.contains("too large") {
            let mut msg = "rapc 验证失败：游戏的核心类字节码过于庞大/分支过多，超过黑莓 COD 转换器的控制流验证信息上限（错误 \"control flow verification information too large\"）。这是黑莓 OS7.1 平台的硬限制，重排/优化字节码无法可靠解决，请换用其它游戏。".to_string();
            if let Ok(j) = Jar::read(jar_in) {
                let tot: u64 = j.entries.iter().map(|e| e.data.len() as u64).sum();
                if tot >= 20 << 20 {
                    msg.push_str(&format!(
                        "另外本游戏解压后 {:.1} MB，即使强行越过本错误，还会撞上单个 COD 最多 255 个分片的设备硬上限，仍然无法安装。",
                        tot as f64 / 1048576.0
                    ));
                }
            }
            return Err(msg);
        }
        if text.contains("code section too large") {
            return Err(
                "rapc 失败：某个类的 COD 代码段仍超过 64KB 上限（错误 \"code section too large\"）。工具已尝试自动拆分大类，但本游戏单个方法/类的核心代码过大，无法再安全拆出；这是黑莓 OS7.1 的平台限制，请换用其它游戏版本。".into(),
            );
        }
        if text.contains("hQ") && text.contains("is null") {
            return Err(
                "rapc 内部错误（this.hQ is null）：本游戏资源总量超过黑莓单个 COD 应用的硬上限（约 20 MB / 最多 255 个兄弟分片，序值 255 被根模块保留，设备 JVM 同样无法加载更多分片）。nolimit/优化等任何手段都无法绕过，请换更小的游戏版本。".into(),
            );
        }
        let tail: Vec<&str> = text.lines().rev().take(15).collect();
        return Err(format!(
            "rapc 失败:\n{}",
            tail.into_iter().rev().collect::<Vec<_>>().join("\n")
        ));
    }
    log(&format!(
        "COD 就绪: {} ({} 字节)",
        cod.display(),
        std::fs::metadata(&cod).map(|m| m.len()).unwrap_or(0)
    ));

    let mut installed = false;
    let do_load = match opts.load {
        LoadMode::Auto => {
            log("==== [5/5] 检测 USB 设备 ====");
            if device_connected() {
                log("已连接黑莓设备，自动装机。");
                true
            } else {
                log("未检测到设备（或端口被桌面管理器占用），COD 已生成，连接手机后点\"装机\"即可。");
                false
            }
        }
        LoadMode::Force => true,
        LoadMode::Never => false,
    };
    if do_load {
        install(&cod, &codname, &tc, &workdir, log)?;
        installed = true;
    }
    Ok((cod.to_string_lossy().into_owned(), installed))
}

/// 仅装机：加载已构建好的 COD。
pub fn install_existing(
    jar_in: &str,
    codname: &str,
    workdir: Option<&str>,
    log: &mut dyn FnMut(&str),
) -> Result<String, String> {
    if !device_connected() {
        return Err("未检测到黑莓设备，请用 USB 连接手机后重试。".into());
    }
    let tc = Toolchain::new();
    let name = if codname.trim().is_empty() {
        default_codname(jar_in)
    } else {
        codname.trim().to_string()
    };
    let wd = match workdir {
        Some(w) if !w.trim().is_empty() => PathBuf::from(w),
        _ => {
            let mut p = Path::new(jar_in)
                .parent()
                .map(|x| x.to_path_buf())
                .unwrap_or_else(|| PathBuf::from("."));
            p.push("bbbuild");
            p
        }
    };
    let cod = wd.join(format!("{name}.cod"));
    if !cod.is_file() {
        return Err(format!("找不到已构建的 {}，请先执行一键构建。", cod.display()));
    }
    log(&format!("---- 装机: {} ----", cod.display()));
    let args = vec!["-u".into(), "erase".into(), name.clone()];
    let _ = run_quiet(&tc.javaloader, &args, &wd);
    install(&cod, &name, &tc, &wd, log)?;
    Ok(cod.to_string_lossy().into_owned())
}
