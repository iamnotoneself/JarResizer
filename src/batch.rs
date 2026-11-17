//! 批量扫描 + 批量自动转换（失败/跳过写 `转换失败.txt`）。

use std::path::{Path, PathBuf};

use crate::convert::{convert, ConvertOptions, KeymapChoice, Mode};
use crate::scan::{self, ScanRecord};

pub struct Problem {
    pub kind: ProblemKind,
    pub name: String,
    pub why: String,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ProblemKind {
    Fail,
    Skip,
}

pub struct BatchConvertResult {
    pub ok: usize,
    pub problems: Vec<Problem>,
    pub n: usize,
    pub txt: Option<String>,
}

fn civil_from_days(z: i64) -> (i32, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as i64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    ((y + if m <= 2 { 1 } else { 0 }) as i32, m, d)
}

fn now_text() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
        + 8 * 3600; // Asia/Shanghai
    let days = secs / 86400;
    let rem = secs % 86400;
    let (y, m, d) = civil_from_days(days);
    format!(
        "{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02}",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

fn unique_out(outdir: &Path, name: &str, src: &str) -> PathBuf {
    let mut outp = outdir.join(name);
    let same = |a: &Path, b: &str| {
        a.to_string_lossy().to_lowercase() == PathBuf::from(b).to_string_lossy().to_lowercase()
    };
    if same(&outp, src) || outp.exists() {
        let p = Path::new(name);
        let stem = p
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| name.to_string());
        let ext = p
            .extension()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| "jar".into());
        let mut k = 2;
        loop {
            let cand = outdir.join(format!("{stem}_{k}.{ext}"));
            if !cand.exists() {
                outp = cand;
                break;
            }
            k += 1;
        }
    }
    outp
}

pub fn batch_convert(
    recs: &[ScanRecord],
    all: &[ScanRecord],
    outdir: &str,
    force_bad: bool,
    log: &mut dyn FnMut(&str),
    progress: &mut dyn FnMut(usize, usize, &str),
) -> BatchConvertResult {
    let mut problems: Vec<Problem> = Vec::new();
    let mut ok_count = 0usize;
    let dir = PathBuf::from(outdir);
    if let Err(e) = std::fs::create_dir_all(&dir) {
        return BatchConvertResult {
            ok: 0,
            problems: vec![Problem {
                kind: ProblemKind::Fail,
                name: "-".into(),
                why: format!("输出目录无效: {e}"),
            }],
            n: recs.len(),
            txt: None,
        };
    }
    for (i, r) in recs.iter().enumerate() {
        progress(i + 1, recs.len(), &r.name);
        if !r.blackberry_ok && force_bad {
            log(&format!(
                "[{}/{}] 强制转换「不可上」: {}",
                i + 1,
                recs.len(),
                r.name
            ));
        }
        let src = &r.jar_path;
        let det = std::fs::read(src)
            .ok()
            .and_then(|_| crate::archive::Jar::read(src).ok())
            .and_then(|j| scan::detect_native_from_assets(&j));
        let (nw, nh) = match det {
            Some((w, h, _)) => (w, h),
            None => match r.native_size_guess {
                Some((w, h)) => {
                    log(&format!(
                        "{}: 自动识别失败，使用预扫描推测 {w}x{h}",
                        r.name
                    ));
                    (w, h)
                }
                None => {
                    let why = "分辨率识别失败: 无法识别原生分辨率（图片/JAD/字节码均无结果）";
                    problems.push(Problem {
                        kind: ProblemKind::Fail,
                        name: r.name.clone(),
                        why: why.into(),
                    });
                    continue;
                }
            },
        };
        let outp = unique_out(&dir, &r.name, src);
        let mut opts = ConvertOptions::default();
        opts.mode = Mode::Auto;
        opts.native_w = nw;
        opts.native_h = nh;
        opts.keymap = KeymapChoice::Auto;
        let res = convert(src, &outp.to_string_lossy(), &opts, log);
        match res {
            Ok(_) => {
                let src_jad = Path::new(src).with_extension("jad");
                if src_jad.is_file() {
                    let dst_jad = outp.with_extension("jad");
                    let size = std::fs::metadata(&outp).map(|m| m.len()).unwrap_or(0);
                    let url = outp
                        .file_name()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_default();
                    if let Err(je) = crate::convert::update_jad(
                        &src_jad.to_string_lossy(),
                        &dst_jad.to_string_lossy(),
                        &url,
                        size,
                    ) {
                        log(&format!("{}: JAD 同步失败（不影响 JAR）: {je}", r.name));
                    }
                }
                ok_count += 1;
            }
            Err(ex) => {
                log(&format!("{} 转换失败: {ex}", r.name));
                problems.push(Problem {
                    kind: ProblemKind::Fail,
                    name: r.name.clone(),
                    why: ex,
                });
            }
        }
    }
    if !force_bad {
        let chosen: std::collections::HashSet<&str> =
            recs.iter().map(|r| r.jar_path.as_str()).collect();
        for r in all {
            if !r.blackberry_ok && !chosen.contains(r.jar_path.as_str()) {
                problems.push(Problem {
                    kind: ProblemKind::Skip,
                    name: r.name.clone(),
                    why: format!(
                        "预扫描判定不兼容，未转换: {}",
                        if r.reason.is_empty() {
                            "未知原因"
                        } else {
                            &r.reason
                        }
                    ),
                });
            }
        }
    }

    let txt_path = dir.join("转换失败.txt");
    let mut txt = None;
    if !problems.is_empty() {
        let mut lines = vec![
            format!("批量转换报告  {}", now_text()),
            format!("成功 {ok_count} 个，失败/跳过 {} 个", problems.len()),
            format!("输出目录: {outdir}"),
            String::new(),
            "========== 转换失败 / 已跳过 ==========".to_string(),
        ];
        for p in &problems {
            let tag = if p.kind == ProblemKind::Fail {
                "[失败]"
            } else {
                "[跳过]"
            };
            lines.push(format!("{tag} {}", p.name));
            lines.push(format!("    原因: {}", p.why));
        }
        let body = format!("\u{feff}{}\n", lines.join("\n"));
        match std::fs::write(&txt_path, body.as_bytes()) {
            Ok(_) => txt = Some(txt_path.to_string_lossy().into_owned()),
            Err(e) => log(&format!("写转换失败.txt 出错: {e}")),
        }
    } else if txt_path.exists() {
        let _ = std::fs::remove_file(&txt_path);
    }

    BatchConvertResult {
        ok: ok_count,
        problems,
        n: recs.len(),
        txt,
    }
}
