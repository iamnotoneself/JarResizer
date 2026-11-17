//! 运行期定位仓库根目录（assets/ tools/）以及启动 Java 工具链。

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

static ROOT: OnceLock<PathBuf> = OnceLock::new();

fn looks_like_root(p: &Path) -> bool {
    p.join("assets").join("jr").join("ScaleCanvas.class").is_file()
        && p.join("tools").join("rapc.jar").is_file()
}

/// 依次尝试：exe 目录及其上级、当前目录及其上级。
pub fn root() -> &'static Path {
    ROOT.get_or_init(|| {
        let mut cands: Vec<PathBuf> = Vec::new();
        if let Ok(exe) = std::env::current_exe() {
            let mut p = exe.parent().map(|x| x.to_path_buf());
            while let Some(d) = p {
                cands.push(d.clone());
                p = d.parent().map(|x| x.to_path_buf());
            }
        }
        if let Ok(cwd) = std::env::current_dir() {
            let mut p = Some(cwd);
            while let Some(d) = p {
                cands.push(d.clone());
                p = d.parent().map(|x| x.to_path_buf());
            }
        }
        for c in &cands {
            if looks_like_root(c) {
                return c.clone();
            }
        }
        cands.into_iter().next().unwrap_or_else(|| PathBuf::from("."))
    })
}

pub fn find_java() -> PathBuf {
    let root = root();
    let mut cands: Vec<PathBuf> = vec![root.join("tools").join("jre").join("bin").join("java.exe")];
    for var in ["JAVA17", "JAVA_HOME"] {
        if let Ok(v) = std::env::var(var) {
            if v.to_lowercase().ends_with("java.exe") {
                cands.push(PathBuf::from(v));
            } else {
                cands.push(PathBuf::from(v).join("bin").join("java.exe"));
            }
        }
    }
    for base in [
        r"C:\Program Files\Microsoft",
        r"C:\Program Files\Eclipse Adoptium",
        r"C:\Program Files\Java",
    ] {
        if let Ok(rd) = std::fs::read_dir(base) {
            for e in rd.flatten() {
                let d = e.path();
                let n = d.file_name().map(|x| x.to_string_lossy().to_lowercase());
                if let Some(n) = n {
                    if n.starts_with("jdk-17") || n.starts_with("jdk-21") {
                        cands.push(d.join("bin").join("java.exe"));
                    }
                }
            }
        }
    }
    if let Ok(path) = std::env::var("PATH") {
        for p in path.split(';') {
            if !p.is_empty() {
                cands.push(PathBuf::from(p).join("java.exe"));
            }
        }
    }
    for c in &cands {
        if c.is_file() {
            return c.clone();
        }
    }
    PathBuf::from("java")
}

pub fn patches_dir() -> Option<PathBuf> {
    let p = root().join("assets").join("patches");
    if p.join("GuardArrayPatcher.class").is_file() {
        Some(p)
    } else {
        None
    }
}

pub fn java_classpath(parts: &[PathBuf]) -> String {
    parts
        .iter()
        .map(|p| p.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(";")
}

fn hide_console(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let _ = cmd;
}

/// 运行子进程，合并 stdout/stderr，返回 (退出码, 文本)。命令本身写入日志。
pub fn run_program(
    program: &Path,
    args: &[String],
    cwd: &Path,
    log: &mut dyn FnMut(&str),
) -> Result<(i32, String), String> {
    let shown = std::iter::once(program.to_string_lossy().into_owned())
        .chain(args.iter().cloned())
        .map(|s| if s.contains(' ') { format!("\"{s}\"") } else { s })
        .collect::<Vec<_>>()
        .join(" ");
    log(&format!("> {shown}"));
    let mut cmd = Command::new(program);
    cmd.args(args)
        .current_dir(cwd)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());
    hide_console(&mut cmd);
    let out = cmd
        .output()
        .map_err(|e| format!("启动 {} 失败: {e}", program.display()))?;
    let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
    let err = String::from_utf8_lossy(&out.stderr);
    if !err.trim().is_empty() {
        text.push_str(&err);
    }
    Ok((out.status.code().unwrap_or(-1), text))
}

/// 后台静默运行（装机检测等）。
pub fn run_quiet(program: &Path, args: &[String], cwd: &Path) -> Option<(i32, String)> {
    let mut cmd = Command::new(program);
    cmd.args(args)
        .current_dir(cwd)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());
    hide_console(&mut cmd);
    let child = cmd.spawn().ok()?;
    let out = child.wait_with_output().ok()?;
    let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    Some((out.status.code().unwrap_or(-1), text))
}

pub fn device_connected() -> bool {
    let loader = root().join("tools").join("javaloader.exe");
    if !loader.is_file() {
        return false;
    }
    let mut cmd = Command::new(&loader);
    cmd.args(["-u", "dir"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());
    hide_console(&mut cmd);
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(_) => return false,
    };
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(12);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    return false;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            Err(_) => {
                let _ = child.kill();
                return false;
            }
        }
    }
    let out = match child.wait_with_output() {
        Ok(o) => o,
        Err(_) => return false,
    };
    if !out.status.success() {
        return false;
    }
    let text = String::from_utf8_lossy(&out.stdout).to_lowercase();
    (text.contains(".cod") || text.contains("modules") || text.contains("guid"))
        && !text.contains("error")
        && !text.contains("unable")
}

fn unique_temp(dir: &Path, prefix: &str) -> PathBuf {
    let pid = std::process::id();
    for i in 0..10000u32 {
        let p = dir.join(format!("{prefix}_{pid}_{i}.jar"));
        if !p.exists() {
            return p;
        }
    }
    dir.join(format!("{prefix}_{pid}.jar"))
}

fn code_size(cf: &crate::classfile::ClassFile) -> usize {
    cf.code_blobs().iter().map(|c| c.len()).sum()
}

/// 大类拆分：调用 patches/ClassSplit.class（ASM），把大方法搬到同包的
/// `jrsN` 兄弟类。失败不阻断转换。
pub fn split_fat_classes(jar_path: &str, log: &mut dyn FnMut(&str)) {
    use crate::consts::{
        SPLIT_CLASS_CODE_LIMIT, SPLIT_CLASS_CODE_TARGET, SPLIT_RESERVED_METHODS,
    };
    let pdir = match patches_dir() {
        Some(p) => p,
        None => return,
    };
    if !pdir.join("ClassSplit.class").is_file() {
        return;
    }
    let java = find_java();
    let root = root();
    let cwd = Path::new(jar_path)
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| root.to_path_buf());

    struct Plan {
        owner: String,
        sib: String,
        specs: Vec<String>,
        total: usize,
        left: usize,
    }
    let jar = match crate::archive::Jar::read(jar_path) {
        Ok(j) => j,
        Err(e) => {
            log(&format!("大类拆分扫描失败（不阻断转换）: {e}"));
            return;
        }
    };
    let mut names: std::collections::HashSet<String> = jar.names().into_iter().collect();
    let mut work: Vec<Plan> = Vec::new();
    for e in &jar.entries {
        if !e.name.ends_with(".class") {
            continue;
        }
        let cf = match crate::classfile::ClassFile::parse(&e.data) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let total = code_size(&cf);
        if total < SPLIT_CLASS_CODE_LIMIT {
            continue;
        }
        let mut cands: Vec<(usize, String, String)> = Vec::new();
        for m in &cf.methods {
            let nm = cf.utf8s(m.name);
            let ds = cf.utf8s(m.desc);
            if SPLIT_RESERVED_METHODS.contains(&nm.as_str()) {
                continue;
            }
            if m.access & 0x0400 != 0 || m.access & 0x0100 != 0 || m.access & 0x0020 != 0 {
                continue;
            }
            let mut sz = 0usize;
            for a in &m.attrs {
                if cf.utf8(a.name) == Some("Code") {
                    if let Some((_, _)) = crate::classfile::code_from_blob(&a.data) {
                        sz = u32::from_be_bytes([
                            a.data[4], a.data[5], a.data[6], a.data[7],
                        ]) as usize;
                    }
                }
            }
            if sz > 0 {
                cands.push((sz, nm, ds));
            }
        }
        cands.sort_by(|a, b| b.cmp(a));
        let mut picked: Vec<String> = Vec::new();
        let mut left = total;
        for (sz, nm, ds) in cands {
            if left <= SPLIT_CLASS_CODE_TARGET {
                break;
            }
            picked.push(format!("{nm}{ds}"));
            left -= sz;
        }
        let owner = e.name[..e.name.len() - 6].to_string();
        if left >= SPLIT_CLASS_CODE_LIMIT {
            log(&format!(
                "警告: 类 {owner} 字节码 {total} 字节，可拆分后仍约 {left} 字节，可能仍超过 COD 代码段上限"
            ));
        }
        if picked.is_empty() {
            log(&format!(
                "警告: 类 {owner} 字节码 {total} 字节超过 COD 代码段上限且没有可安全拆出的方法，rapc 可能失败"
            ));
            continue;
        }
        let pkg = owner.rsplit_once('/').map(|(p, _)| p).unwrap_or("");
        let mut i = 1;
        let sib = loop {
            let s = if pkg.is_empty() {
                format!("jrs{i}")
            } else {
                format!("{pkg}/jrs{i}")
            };
            if !names.contains(&format!("{s}.class")) {
                break s;
            }
            i += 1;
        };
        names.insert(format!("{sib}.class"));
        work.push(Plan {
            owner,
            sib,
            specs: picked,
            total,
            left,
        });
    }
    if work.is_empty() {
        return;
    }
    let cp = java_classpath(&[pdir.clone(), pdir.join("asm.jar")]);
    for p in work {
        let tmp = unique_temp(&cwd, "split");
        let mut args = vec![
            "-cp".to_string(),
            cp.clone(),
            "ClassSplit".to_string(),
            jar_path.to_string(),
            tmp.to_string_lossy().into_owned(),
            p.owner.clone(),
            p.sib.clone(),
        ];
        args.extend(p.specs.iter().cloned());
        match run_program(&java, &args, &cwd, log) {
            Ok((rc, text)) => {
                if rc != 0 || !text.contains("split OK") {
                    let tail: Vec<&str> = text.lines().rev().take(3).collect();
                    log(&format!(
                        "大类拆分失败（不阻断转换）: {}",
                        tail.into_iter().rev().collect::<Vec<_>>().join("\n")
                    ));
                    let _ = std::fs::remove_file(&tmp);
                    continue;
                }
                if std::fs::rename(&tmp, jar_path).is_err() {
                    log("大类拆分失败（无法替换 jar）");
                } else {
                    log(&format!(
                        "大类拆分: {}({}KB) 已将 {} 个方法移至 {}（保留约 {}KB），以绕过 COD 64KB 代码段限制",
                        p.owner,
                        p.total / 1024,
                        p.specs.len(),
                        p.sib,
                        p.left / 1024
                    ));
                }
            }
            Err(e) => log(&format!("大类拆分运行失败（不阻断转换）: {e}")),
        }
        let _ = std::fs::remove_file(&tmp);
    }
}

/// 游戏原包 bug 兜底（ASM GuardArrayPatcher）。无命中时磁盘内容不变。
pub fn run_game_bytecode_patches(jar_path: &str, log: &mut dyn FnMut(&str)) {
    let pdir = patches_dir();
    let java = find_java();
    let (pdir, java) = match pdir {
        Some(p) => (p, java),
        None => {
            log("跳过游戏字节码兜底补丁（未找到补丁类）");
            return;
        }
    };
    let cwd = Path::new(jar_path)
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| root().to_path_buf());
    let tmp = unique_temp(&cwd, "gpatch");
    let cp = java_classpath(&[pdir.clone(), pdir.join("asm.jar")]);
    let args = vec![
        "-cp".to_string(),
        cp,
        "GuardArrayPatcher".to_string(),
        jar_path.to_string(),
        tmp.to_string_lossy().into_owned(),
    ];
    match run_program(&java, &args, &cwd, log) {
        Ok((_rc, text)) => {
            let mut hits = 0usize;
            for ln in text.lines() {
                if ln.contains("guarded array load") {
                    if let Some(rest) = ln.split(':').nth(1) {
                        if let Some(tok) = rest.trim().split_whitespace().next() {
                            if let Ok(v) = tok.parse::<usize>() {
                                hits = v;
                            }
                        }
                    }
                }
            }
            if hits > 0 {
                if std::fs::rename(&tmp, jar_path).is_ok() {
                    log(&format!("游戏自带 bug 兜底: 已守卫 {hits} 处越界数组访问"));
                } else {
                    log("游戏字节码兜底补丁: 无法替换 jar");
                }
            } else {
                log("游戏字节码兜底补丁: 无命中");
            }
        }
        Err(e) => log(&format!("游戏字节码兜底补丁运行失败（不阻断转换）: {e}")),
    }
    let _ = std::fs::remove_file(&tmp);
}
