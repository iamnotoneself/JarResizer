//! egui 图形界面：浅色 + 蓝色强调，左栏设置、右侧日志。
//! 耗时任务全部在后台线程执行，通过 mpsc 回传日志与完成事件。

use std::collections::HashSet;
use std::path::Path;
use std::sync::mpsc::{channel, Receiver, Sender};

use eframe::egui::{self, Color32, Margin, RichText, Stroke};

use crate::batch::{batch_convert, BatchConvertResult};
use crate::cod::{build_cod, install_existing, LoadMode, CodOptions};
use crate::convert::{convert, update_jad, ConvertOptions, KeymapChoice, Mode};
use crate::scan::{self, ScanRecord};

const BG: Color32 = Color32::from_rgb(243, 245, 250);
const CARD: Color32 = Color32::WHITE;
const INK: Color32 = Color32::from_rgb(28, 36, 48);
const SUB: Color32 = Color32::from_rgb(107, 122, 144);
const ACCENT: Color32 = Color32::from_rgb(47, 101, 208);
const BORDER: Color32 = Color32::from_rgb(226, 232, 240);
const GOOD: Color32 = Color32::from_rgb(31, 138, 76);
const BAD: Color32 = Color32::from_rgb(192, 57, 43);
const WARN: Color32 = Color32::from_rgb(184, 134, 11);

const KEY_PRESETS: &[(&str, &str)] = &[
    (
        "黑莓键帽数字（WER/SDF/ZXC=1-9）",
        "w=1,e=2,r=3,s=4,d=5,f=6,z=7,x=8,c=9,space=5,enter=-6,esc=-7",
    ),
    (
        "厂商游戏码（WASD移动 空格动作）",
        "w=-1,s=-2,a=-3,d=-4,space=-5,enter=-6,esc=-7",
    ),
    ("仅移动+动作（WASD + 空格）", "w=-1,s=-2,a=-3,d=-4,space=-5"),
];

const KEYMAP_CHOICES: &[&str] = &[
    "自动识别（推荐）",
    KEY_PRESETS[0].0,
    KEY_PRESETS[1].0,
    KEY_PRESETS[2].0,
    "自定义映射",
    "不映射（用游戏原版按键）",
];

fn keycode(s: &str) -> Result<i32, String> {
    let low = s.to_lowercase();
    match low.as_str() {
        "space" => return Ok(32),
        "enter" => return Ok(10),
        "esc" | "escape" => return Ok(27),
        "tab" => return Ok(9),
        "del" => return Ok(8),
        "alt" | "shift" => return Ok(-128),
        _ => {}
    }
    if low.len() == 1 && low.chars().next().map(|c| c.is_ascii_alphabetic()).unwrap_or(false) {
        return Ok(low.as_bytes()[0] as i32);
    }
    low.parse::<i32>()
        .map_err(|_| format!("无法识别的键: {s}"))
}

/// 'w=2,s=8,space=5' -> [119,50,115,56,32,53]
pub fn parse_keymap(text: &str) -> Result<Vec<i32>, String> {
    let mut out = Vec::new();
    for tok in text.split(',') {
        let tok = tok.trim();
        if tok.is_empty() {
            continue;
        }
        let (a, b) = tok
            .split_once('=')
            .ok_or_else(|| format!("缺少等号: {tok}"))?;
        if a.trim().is_empty() || b.trim().is_empty() {
            return Err(format!("键名不能为空: {tok}"));
        }
        out.push(keycode(a.trim())?);
        out.push(keycode(b.trim())?);
    }
    Ok(out)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tab {
    Convert,
    Batch,
    Help,
}

enum Msg {
    Log(String),
    ConvertDone(Result<String, String>),
    CodDone(Result<(String, bool), String>),
    InstallDone(Result<String, String>),
    Info(Box<InfoResult>),
    Device(bool),
    BatchProgress(usize, usize, String),
    BatchDone(Vec<ScanRecord>),
    BConvProgress(usize, usize, String),
    BConvDone(Box<BatchConvertResult>),
}

struct InfoResult {
    info: String,
    risks: Vec<String>,
    native: Option<(i32, i32)>,
    ok: bool,
}

enum Action {
    PickJar,
    PickOut,
    Convert,
    BuildCod,
    BuildOnly,
    Install,
    Check,
    BatchPickDir,
    BatchPickOut,
    BatchScan,
    BatchSelected,
    BatchAll,
}

fn send(tx: &Sender<Msg>, ctx: &egui::Context, m: Msg) {
    let _ = tx.send(m);
    ctx.request_repaint();
}

pub struct App {
    tx: Sender<Msg>,
    rx: Receiver<Msg>,
    ctx: egui::Context,
    tab: Tab,
    busy: bool,
    status: String,
    device: Option<bool>,

    jar: String,
    out: String,
    fixed: bool,
    auto_size: bool,
    nw: String,
    nh: String,
    preset_idx: usize,
    tw: String,
    th: String,
    keymap_idx: usize,
    keymap_text: String,
    fit_idx: usize,
    crop: String,
    sync_jad: bool,
    codname: String,

    log: Vec<String>,
    info: String,
    risks: Vec<String>,

    batch_dir: String,
    batch_out: String,
    recursive: bool,
    force_bad: bool,
    results: Vec<ScanRecord>,
    selected: HashSet<usize>,
    batch_busy: bool,
    batch_done: usize,
    batch_total: usize,
    batch_status: String,
    report: String,
}

impl App {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        install_fonts(&cc.egui_ctx);
        apply_style(&cc.egui_ctx);
        let (tx, rx) = channel();
        App {
            tx,
            rx,
            ctx: cc.egui_ctx.clone(),
            tab: Tab::Convert,
            busy: false,
            status: "就绪".into(),
            device: None,
            jar: String::new(),
            out: String::new(),
            fixed: false,
            auto_size: true,
            nw: "360".into(),
            nh: "640".into(),
            preset_idx: 0,
            tw: "480".into(),
            th: "640".into(),
            keymap_idx: 0,
            keymap_text: KEY_PRESETS[0].1.to_string(),
            fit_idx: 0,
            crop: String::new(),
            sync_jad: true,
            codname: String::new(),
            log: vec![
                "JarResizer 2.0 · J2ME 360x640 → BlackBerry 9930 (480x640)".into(),
                "选择 jar 后可先点“快速检查”，再转换或一键构建 COD。".into(),
            ],
            info: String::new(),
            risks: Vec::new(),
            batch_dir: String::new(),
            batch_out: String::new(),
            recursive: true,
            force_bad: false,
            results: Vec::new(),
            selected: HashSet::new(),
            batch_busy: false,
            batch_done: 0,
            batch_total: 0,
            batch_status: String::new(),
            report: String::new(),
        }
    }

    fn push_log(&mut self, s: &str) {
        self.log.push(s.to_string());
        if self.log.len() > 5000 {
            self.log.drain(0..1000);
        }
    }

    fn drain(&mut self) {
        while let Ok(m) = self.rx.try_recv() {
            match m {
                Msg::Log(s) => self.push_log(&s),
                Msg::ConvertDone(r) => {
                    self.busy = false;
                    match r {
                        Ok(s) => {
                            self.push_log(&format!("✔ {s}"));
                            self.status = s;
                        }
                        Err(e) => {
                            self.push_log(&format!("✘ 转换失败: {e}"));
                            self.status = "转换失败".into();
                            self.risks.push(e);
                        }
                    }
                }
                Msg::CodDone(r) => {
                    self.busy = false;
                    match r {
                        Ok((cod, installed)) => {
                            self.push_log(&format!("✔ COD: {cod}"));
                            self.status = if installed {
                                "已装机，请在手机上重进游戏".into()
                            } else {
                                "COD 已生成".into()
                            };
                        }
                        Err(e) => {
                            self.push_log(&format!("✘ 构建 COD 失败: {e}"));
                            self.status = "构建 COD 失败".into();
                            self.risks.push(e);
                        }
                    }
                }
                Msg::InstallDone(r) => {
                    self.busy = false;
                    match r {
                        Ok(s) => {
                            self.push_log(&format!("✔ 装机完成: {s}"));
                            self.status = "装机完成".into();
                        }
                        Err(e) => {
                            self.push_log(&format!("✘ 装机失败: {e}"));
                            self.status = "装机失败".into();
                        }
                    }
                }
                Msg::Info(i) => {
                    self.busy = false;
                    self.info = i.info.clone();
                    self.risks = i.risks.clone();
                    if self.auto_size {
                        if let Some((w, h)) = i.native {
                            self.nw = w.to_string();
                            self.nh = h.to_string();
                        }
                    }
                    self.push_log(&format!(
                        "检查: {} {}",
                        i.info,
                        if i.ok { "" } else { "（不可直接上黑莓）" }
                    ));
                    self.status = "检查完成".into();
                }
                Msg::Device(ok) => {
                    self.device = Some(ok);
                    self.push_log(if ok {
                        "USB: 检测到黑莓设备"
                    } else {
                        "USB: 未检测到设备"
                    });
                    self.status = if ok {
                        "设备已连接".into()
                    } else {
                        "未检测到设备".into()
                    };
                }
                Msg::BatchProgress(i, n, name) => {
                    self.batch_done = i;
                    self.batch_total = n;
                    self.batch_status = name;
                }
                Msg::BatchDone(res) => {
                    self.batch_busy = false;
                    self.batch_total = res.len();
                    self.batch_done = res.len();
                    self.selected = res
                        .iter()
                        .enumerate()
                        .filter(|(_, r)| r.blackberry_ok)
                        .map(|(i, _)| i)
                        .collect();
                    let ok = res.iter().filter(|r| r.blackberry_ok).count();
                    self.batch_status = format!("扫描完成: {ok}/{} 可上黑莓", res.len());
                    self.push_log(&self.batch_status.clone());
                    self.results = res;
                }
                Msg::BConvProgress(i, n, name) => {
                    self.batch_done = i;
                    self.batch_total = n;
                    self.batch_status = name;
                }
                Msg::BConvDone(r) => {
                    self.batch_busy = false;
                    self.batch_done = r.n;
                    self.batch_total = r.n;
                    self.report = r.txt.clone().unwrap_or_default();
                    self.batch_status = format!(
                        "转换完成: 成功 {} / {}，失败或跳过 {}",
                        r.ok,
                        r.n,
                        r.problems.len()
                    );
                    self.push_log(&self.batch_status.clone());
                    for p in &r.problems {
                        self.push_log(&format!(
                            "  {} {}: {}",
                            if p.kind == crate::batch::ProblemKind::Fail {
                                "[失败]"
                            } else {
                                "[跳过]"
                            },
                            p.name,
                            p.why
                        ));
                    }
                }
            }
        }
    }

    fn resolve_native(&self) -> Result<(i32, i32), String> {
        if self.auto_size {
            let jar = crate::archive::Jar::read(&self.jar)?;
            match scan::detect_native_from_assets(&jar) {
                Some((w, h, _why)) => Ok((w, h)),
                None => Err("无法自动识别分辨率，请取消“自动识别”并手动填写".into()),
            }
        } else {
            let w: i32 = self
                .nw
                .trim()
                .parse()
                .map_err(|_| "原生宽度必须是数字")?;
            let h: i32 = self
                .nh
                .trim()
                .parse()
                .map_err(|_| "原生高度必须是数字")?;
            if !(64..=4000).contains(&w) || !(64..=4000).contains(&h) {
                return Err("原生分辨率超出合理范围 (64-4000)".into());
            }
            Ok((w, h))
        }
    }

    fn target(&self) -> Result<(Option<i32>, Option<i32>), String> {
        if !self.fixed {
            return Ok((None, None));
        }
        let w: i32 = self.tw.trim().parse().map_err(|_| "目标宽度必须是数字")?;
        let h: i32 = self.th.trim().parse().map_err(|_| "目标高度必须是数字")?;
        if w <= 0 || h <= 0 {
            return Err("目标分辨率必须为正数".into());
        }
        Ok((Some(w), Some(h)))
    }

    fn keymap(&self) -> Result<KeymapChoice, String> {
        match self.keymap_idx {
            0 => Ok(KeymapChoice::Auto),
            1 | 2 | 3 => Ok(KeymapChoice::Custom(parse_keymap(
                KEY_PRESETS[self.keymap_idx - 1].1,
            )?)),
            4 => Ok(KeymapChoice::Custom(parse_keymap(&self.keymap_text)?)),
            _ => Ok(KeymapChoice::Off),
        }
    }

    fn fit_value(&self) -> String {
        match self.fit_idx {
            1 => "fit".into(),
            2 => "stretch".into(),
            3 => "cover".into(),
            _ => String::new(),
        }
    }

    fn default_out(&self) -> String {
        if !self.out.trim().is_empty() {
            return self.out.clone();
        }
        if self.jar.is_empty() {
            return String::new();
        }
        let p = Path::new(&self.jar);
        let base = p
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| "game".into());
        p.with_file_name(format!("{base}_bb.jar"))
            .to_string_lossy()
            .into_owned()
    }

    // ------------------------------------------------------------ actions

    fn handle(&mut self, a: Action) {
        match a {
            Action::PickJar => {
                if let Some(p) = rfd::FileDialog::new()
                    .add_filter("JAR", &["jar"])
                    .pick_file()
                {
                    self.jar = p.to_string_lossy().into_owned();
                    let p = Path::new(&self.jar);
                    let base = p
                        .file_stem()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_else(|| "game".into());
                    self.out = p
                        .with_file_name(format!("{base}_bb.jar"))
                        .to_string_lossy()
                        .into_owned();
                    self.start_check();
                }
            }
            Action::PickOut => {
                if let Some(p) = rfd::FileDialog::new()
                    .add_filter("JAR", &["jar"])
                    .set_file_name(
                        Path::new(&self.default_out())
                            .file_name()
                            .map(|s| s.to_string_lossy().into_owned())
                            .unwrap_or_else(|| "game_bb.jar".into()),
                    )
                    .save_file()
                {
                    self.out = p.to_string_lossy().into_owned();
                }
            }
            Action::Convert => self.start_convert(),
            Action::BuildCod => self.start_cod(LoadMode::Auto, false),
            Action::BuildOnly => self.start_cod(LoadMode::Never, false),
            Action::Install => self.start_install(),
            Action::Check => self.start_check(),
            Action::BatchPickDir => {
                if let Some(p) = rfd::FileDialog::new().pick_folder() {
                    self.batch_dir = p.to_string_lossy().into_owned();
                    if self.batch_out.is_empty() {
                        self.batch_out = p
                            .join("bb_out")
                            .to_string_lossy()
                            .into_owned();
                    }
                }
            }
            Action::BatchPickOut => {
                if let Some(p) = rfd::FileDialog::new().pick_folder() {
                    self.batch_out = p.to_string_lossy().into_owned();
                }
            }
            Action::BatchScan => self.start_batch_scan(),
            Action::BatchSelected => self.start_batch_convert(false),
            Action::BatchAll => self.start_batch_convert(true),
        }
    }

    fn start_check(&mut self) {
        if self.jar.is_empty() || !Path::new(&self.jar).is_file() || self.busy {
            return;
        }
        let jar = self.jar.clone();
        let tx = self.tx.clone();
        let ctx = self.ctx.clone();
        self.busy = true;
        self.status = "检查 jar…".into();
        std::thread::spawn(move || {
            let integ = scan::scan_package_integrity(&jar);
            let mut risks: Vec<String> = Vec::new();
            for w in &integ.warnings {
                risks.push(format!("提示: {w}"));
            }
            for f in &integ.fatal {
                risks.push(format!("不可上: {f}"));
            }
            let j = crate::archive::Jar::read(&jar).ok();
            let mut native = None;
            let mut scheme = None;
            if let Some(jar) = &j {
                if let Some((w, h, why)) = scan::detect_native_from_assets(jar) {
                    native = Some((w, h));
                    scan::scan_rapc_risks(jar, &mut |s| risks.push(s.to_string()));
                    scheme = scan::detect_key_scheme(jar);
                    let _ = why;
                }
            } else {
                risks.push("jar 无法读取".into());
            }
            let scheme_txt = match scheme.as_deref() {
                Some("digit") => "数字键",
                Some("vendor") => "厂商码",
                _ => "未知",
            };
            let nat = native
                .map(|(w, h)| format!("{w}x{h}"))
                .unwrap_or_else(|| "未知".into());
            let info = format!(
                "原生 {nat} · 按键 {scheme_txt} · 包完整性 {}",
                if integ.runnable { "通过" } else { "不通过" }
            );
            send(
                &tx,
                &ctx,
                Msg::Info(Box::new(InfoResult {
                    info,
                    risks,
                    native,
                    ok: integ.runnable,
                })),
            );
        });
    }

    fn start_convert(&mut self) {
        if self.busy {
            return;
        }
        let out = self.default_out();
        if self.jar.is_empty() || !Path::new(&self.jar).is_file() {
            self.status = "请先选择 JAR".into();
            return;
        }
        if out.is_empty() {
            self.status = "请选择输出路径".into();
            return;
        }
        let (nw, nh) = match self.resolve_native() {
            Ok(v) => v,
            Err(e) => {
                self.push_log(&format!("✘ {e}"));
                self.status = e;
                return;
            }
        };
        let keymap = match self.keymap() {
            Ok(v) => v,
            Err(e) => {
                self.push_log(&format!("✘ 键位格式错误: {e}"));
                self.status = format!("键位格式错误: {e}");
                return;
            }
        };
        let (tw, th) = match self.target() {
            Ok(v) => v,
            Err(e) => {
                self.push_log(&format!("✘ {e}"));
                self.status = e;
                return;
            }
        };
        let mut opts = ConvertOptions::default();
        opts.mode = if self.fixed { Mode::Fixed } else { Mode::Auto };
        opts.native_w = nw;
        opts.native_h = nh;
        opts.target_w = tw;
        opts.target_h = th;
        opts.keymap = if self.fixed {
            KeymapChoice::Off
        } else {
            keymap
        };
        opts.fit = self.fit_value();
        opts.crop = self.crop.trim().to_string();
        let jar = self.jar.clone();
        let out2 = out.clone();
        let sync = self.sync_jad;
        let tx = self.tx.clone();
        let ctx = self.ctx.clone();
        self.busy = true;
        self.status = "转换中…".into();
        self.push_log(&format!("---- 转换 {jar} ----"));
        std::thread::spawn(move || {
            let txc = tx.clone();
            let ctxc = ctx.clone();
            let mut logf = |s: &str| send(&txc, &ctxc, Msg::Log(s.to_string()));
            let r = (|| -> Result<String, String> {
                let size = convert(&jar, &out2, &opts, &mut logf)?;
                if sync {
                    let src_jad = Path::new(&jar).with_extension("jad");
                    if src_jad.is_file() {
                        let dst_jad = Path::new(&out2).with_extension("jad");
                        let url = Path::new(&out2)
                            .file_name()
                            .map(|s| s.to_string_lossy().into_owned())
                            .unwrap_or_default();
                        update_jad(
                            &src_jad.to_string_lossy(),
                            &dst_jad.to_string_lossy(),
                            &url,
                            size,
                        )?;
                        logf(&format!("已同步 JAD: {}", dst_jad.display()));
                    } else {
                        logf("源 JAR 旁未找到 JAD，跳过 JAD 更新");
                    }
                }
                Ok(format!(
                    "转换完成: {} ({:.1} KB)",
                    Path::new(&out2)
                        .file_name()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_else(|| out2.clone()),
                    size as f64 / 1024.0
                ))
            })();
            send(&tx, &ctx, Msg::ConvertDone(r));
        });
    }

    fn start_cod(&mut self, load: LoadMode, reuse: bool) {
        if self.busy {
            return;
        }
        if self.jar.is_empty() || !Path::new(&self.jar).is_file() {
            self.status = "请先选择 JAR".into();
            return;
        }
        let (nw, nh) = match self.resolve_native() {
            Ok(v) => v,
            Err(e) => {
                self.push_log(&format!("✘ {e}"));
                self.status = e;
                return;
            }
        };
        let keymap = match self.keymap() {
            Ok(v) => v,
            Err(e) => {
                self.push_log(&format!("✘ 键位格式错误: {e}"));
                self.status = format!("键位格式错误: {e}");
                return;
            }
        };
        let opts = CodOptions {
            codname: self.codname.trim().to_string(),
            workdir: None,
            mode: Mode::Auto,
            keymap,
            fit: self.fit_value(),
            crop: self.crop.trim().to_string(),
            load,
            reuse_conv: reuse,
        };
        let jar = self.jar.clone();
        let tx = self.tx.clone();
        let ctx = self.ctx.clone();
        self.busy = true;
        self.status = "黑莓流水线运行中…".into();
        self.push_log("---- 一键构建 BlackBerry COD ----");
        std::thread::spawn(move || {
            let txc = tx.clone();
            let ctxc = ctx.clone();
            let mut logf = |s: &str| send(&txc, &ctxc, Msg::Log(s.to_string()));
            let r = build_cod(&jar, nw, nh, &opts, &mut logf);
            send(&tx, &ctx, Msg::CodDone(r));
        });
    }

    fn start_install(&mut self) {
        if self.busy {
            return;
        }
        if self.jar.is_empty() {
            self.status = "请先选择 JAR（用于定位 bbbuild 目录）".into();
            return;
        }
        let jar = self.jar.clone();
        let name = self.codname.clone();
        let tx = self.tx.clone();
        let ctx = self.ctx.clone();
        self.busy = true;
        self.status = "装机中…".into();
        self.push_log("---- 装机（已有 COD） ----");
        std::thread::spawn(move || {
            let txc = tx.clone();
            let ctxc = ctx.clone();
            let mut logf = |s: &str| send(&txc, &ctxc, Msg::Log(s.to_string()));
            let r = install_existing(&jar, &name, None, &mut logf);
            send(&tx, &ctx, Msg::InstallDone(r));
        });
    }

    fn check_device(&mut self) {
        let tx = self.tx.clone();
        let ctx = self.ctx.clone();
        self.status = "检测设备…".into();
        std::thread::spawn(move || {
            let ok = crate::tools::device_connected();
            send(&tx, &ctx, Msg::Device(ok));
        });
    }

    fn start_batch_scan(&mut self) {
        if self.batch_busy {
            return;
        }
        if self.batch_dir.is_empty() {
            self.batch_status = "请先选择目录".into();
            return;
        }
        let dir = self.batch_dir.clone();
        let rec = self.recursive;
        let tx = self.tx.clone();
        let ctx = self.ctx.clone();
        self.batch_busy = true;
        self.batch_status = "扫描中…".into();
        std::thread::spawn(move || {
            let txc = tx.clone();
            let ctxc = ctx.clone();
            let mut logf = |s: &str| send(&txc, &ctxc, Msg::Log(s.to_string()));
            let txp = tx.clone();
            let ctxp = ctx.clone();
            let mut prog = |i: usize, n: usize, name: &str| {
                send(&txp, &ctxp, Msg::BatchProgress(i, n, name.to_string()))
            };
            let res = scan::batch_scan(&dir, rec, &mut logf, &mut prog);
            send(&tx, &ctx, Msg::BatchDone(res));
        });
    }

    fn start_batch_convert(&mut self, all: bool) {
        if self.batch_busy || self.results.is_empty() {
            return;
        }
        if self.batch_out.is_empty() {
            self.batch_status = "请选择输出目录".into();
            return;
        }
        let recs: Vec<ScanRecord> = if all {
            self.results
                .iter()
                .filter(|r| self.force_bad || r.blackberry_ok)
                .cloned()
                .collect()
        } else {
            let mut idx: Vec<usize> = self.selected.iter().copied().collect();
            idx.sort();
            idx.into_iter()
                .filter_map(|i| self.results.get(i).cloned())
                .filter(|r| self.force_bad || r.blackberry_ok)
                .collect()
        };
        if recs.is_empty() {
            self.batch_status = "没有可转换的 jar".into();
            return;
        }
        let allres = self.results.clone();
        let outdir = self.batch_out.clone();
        let force = self.force_bad;
        let tx = self.tx.clone();
        let ctx = self.ctx.clone();
        self.batch_busy = true;
        self.batch_done = 0;
        self.batch_total = recs.len();
        self.batch_status = "批量转换中…".into();
        std::thread::spawn(move || {
            let txc = tx.clone();
            let ctxc = ctx.clone();
            let mut logf = |s: &str| send(&txc, &ctxc, Msg::Log(s.to_string()));
            let txp = tx.clone();
            let ctxp = ctx.clone();
            let mut prog = |i: usize, n: usize, name: &str| {
                send(&txp, &ctxp, Msg::BConvProgress(i, n, name.to_string()))
            };
            let r = batch_convert(&recs, &allres, &outdir, force, &mut logf, &mut prog);
            send(&tx, &ctx, Msg::BConvDone(Box::new(r)));
        });
    }

    // ---------------------------------------------------------------- ui

    fn ui_top(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.label(RichText::new("JarResizer").size(21.0).strong().color(INK));
            ui.label(RichText::new("2.0").size(13.0).color(ACCENT).strong());
            ui.add_space(4.0);
            ui.label(
                RichText::new("J2ME → BlackBerry 9930 (480x640)")
                    .size(12.0)
                    .color(SUB),
            );
            ui.add_space(18.0);
            for (t, label) in [
                (Tab::Convert, "转换"),
                (Tab::Batch, "批量扫描"),
                (Tab::Help, "帮助"),
            ] {
                if ui
                    .selectable_label(self.tab == t, RichText::new(label).size(14.0))
                    .clicked()
                {
                    self.tab = t;
                }
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let (txt, col) = match self.device {
                    Some(true) => ("● 设备已连接", GOOD),
                    Some(false) => ("● 设备未连接", SUB),
                    None => ("○ 设备未检测", SUB),
                };
                ui.label(RichText::new(txt).size(12.0).color(col));
            });
        });
    }

    fn field<'a>(ui: &mut egui::Ui, label: &str, add: impl FnOnce(&mut egui::Ui)) {
        ui.horizontal(|ui| {
            ui.add_sized(
                [88.0, 24.0],
                egui::Label::new(RichText::new(label).color(SUB).size(13.0)),
            );
            add(ui);
        });
    }

    fn card(ui: &mut egui::Ui, title: &str, add: impl FnOnce(&mut egui::Ui)) {
        let frame = egui::Frame {
            fill: CARD,
            stroke: Stroke::new(1.0_f32, BORDER),
            inner_margin: Margin::same(12),
            corner_radius: egui::CornerRadius::same(8),
            ..Default::default()
        };
        frame.show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.label(RichText::new(title).size(13.5).strong().color(INK));
            ui.add_space(7.0);
            add(ui);
        });
        ui.add_space(10.0);
    }

    fn primary(text: &str) -> egui::Button<'static> {
        egui::Button::new(RichText::new(text).color(Color32::WHITE).strong())
            .fill(ACCENT)
            .min_size(egui::vec2(120.0, 32.0))
    }

    fn secondary(text: &str) -> egui::Button<'static> {
        egui::Button::new(RichText::new(text).color(INK))
            .fill(Color32::from_rgb(247, 249, 252))
            .stroke(Stroke::new(1.0_f32, Color32::from_rgb(205, 216, 232)))
            .min_size(egui::vec2(108.0, 30.0))
    }

    fn ui_convert(&mut self, ctx: &egui::Context) -> Option<Action> {
        let mut act = None;
        egui::SidePanel::left("settings_panel")
            .resizable(true)
            .default_width(438.0)
            .width_range(360.0..=560.0)
            .frame(egui::Frame {
                fill: BG,
                inner_margin: Margin::symmetric(14, 12),
                ..Default::default()
            })
            .show(ctx, |ui| {
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        Self::card(ui, "文件", |ui| {
                            Self::field(ui, "游戏 JAR", |ui| {
                                ui.add(
                                    egui::TextEdit::singleline(&mut self.jar)
                                        .desired_width(ui.available_width() - 62.0),
                                );
                                if ui.button("选择").clicked() {
                                    act = Some(Action::PickJar);
                                }
                            });
                            Self::field(ui, "输出 JAR", |ui| {
                                ui.add(
                                    egui::TextEdit::singleline(&mut self.out)
                                        .hint_text("留空则自动生成 *_bb.jar")
                                        .desired_width(ui.available_width() - 62.0),
                                );
                                if ui.button("另存").clicked() {
                                    act = Some(Action::PickOut);
                                }
                            });
                            ui.add_space(3.0);
                            ui.horizontal(|ui| {
                                if ui.add(Self::secondary("快速检查")).clicked() {
                                    act = Some(Action::Check);
                                }
                                ui.label(
                                    RichText::new("检查完整性 / 分辨率 / 键位 / rapc 风险")
                                        .size(11.5)
                                        .color(SUB),
                                );
                            });
                        });

                        Self::card(ui, "画面适配", |ui| {
                            ui.radio_value(
                                &mut self.fixed,
                                false,
                                RichText::new("自动适配（推荐）").size(13.0),
                            );
                            ui.radio_value(
                                &mut self.fixed,
                                true,
                                RichText::new("固定模式（同长度改尺寸常量）").size(13.0),
                            );
                            ui.add_space(6.0);
                            ui.checkbox(
                                &mut self.auto_size,
                                RichText::new("自动识别原生分辨率").size(13.0),
                            );
                            ui.horizontal(|ui| {
                                ui.add_enabled_ui(!self.auto_size, |ui| {
                                    ui.add(
                                        egui::TextEdit::singleline(&mut self.nw)
                                            .desired_width(58.0),
                                    );
                                    ui.label("×");
                                    ui.add(
                                        egui::TextEdit::singleline(&mut self.nh)
                                            .desired_width(58.0),
                                    );
                                    ui.label(RichText::new("原生").size(12.0).color(SUB));
                                });
                            });
                            ui.add_space(4.0);
                            ui.add_enabled_ui(self.fixed, |ui| {
                                Self::field(ui, "目标分辨率", |ui| {
                                    let presets: Vec<String> = crate::consts::PRESETS
                                        .iter()
                                        .map(|(w, h)| format!("{w}x{h}"))
                                        .collect();
                                    egui::ComboBox::from_id_salt("preset")
                                        .selected_text(format!("{}x{}", self.tw, self.th))
                                        .width(110.0)
                                        .show_ui(ui, |ui| {
                                            for (i, p) in presets.iter().enumerate() {
                                                if ui
                                                    .selectable_label(
                                                        self.preset_idx == i,
                                                        p,
                                                    )
                                                    .clicked()
                                                {
                                                    self.preset_idx = i;
                                                    self.tw = crate::consts::PRESETS[i]
                                                        .0
                                                        .to_string();
                                                    self.th = crate::consts::PRESETS[i]
                                                        .1
                                                        .to_string();
                                                }
                                            }
                                        });
                                    ui.add(
                                        egui::TextEdit::singleline(&mut self.tw)
                                            .desired_width(52.0),
                                    );
                                    ui.label("×");
                                    ui.add(
                                        egui::TextEdit::singleline(&mut self.th)
                                            .desired_width(52.0),
                                    );
                                });
                            });
                            Self::field(ui, "铺满策略", |ui| {
                                egui::ComboBox::from_id_salt("fit")
                                    .selected_text(
                                        [
                                            "等比留黑边",
                                            "铺满（拉伸）",
                                            "铺满（裁剪边缘）",
                                        ][self.fit_idx.min(3)],
                                    )
                                    .show_ui(ui, |ui| {
                                        for (i, s) in [
                                            "等比留黑边",
                                            "铺满（拉伸）",
                                            "铺满（裁剪边缘）",
                                        ]
                                        .iter()
                                        .enumerate()
                                        {
                                            ui.selectable_value(
                                                &mut self.fit_idx,
                                                i,
                                                *s,
                                            );
                                        }
                                    });
                            });
                            Self::field(ui, "内容区裁剪", |ui| {
                                ui.add(
                                    egui::TextEdit::singleline(&mut self.crop)
                                        .hint_text("x,y,w,h（可留空）")
                                        .desired_width(ui.available_width()),
                                );
                            });
                        });

                        Self::card(ui, "按键", |ui| {
                            Self::field(ui, "键位方案", |ui| {
                                let mut changed = false;
                                egui::ComboBox::from_id_salt("keymap")
                                    .selected_text(KEYMAP_CHOICES[self.keymap_idx])
                                    .width(ui.available_width())
                                    .show_ui(ui, |ui| {
                                        for (i, s) in KEYMAP_CHOICES.iter().enumerate() {
                                            if ui
                                                .selectable_label(
                                                    self.keymap_idx == i,
                                                    *s,
                                                )
                                                .clicked()
                                            {
                                                self.keymap_idx = i;
                                                changed = true;
                                            }
                                        }
                                    });
                                if changed && (1..=3).contains(&self.keymap_idx) {
                                    self.keymap_text =
                                        KEY_PRESETS[self.keymap_idx - 1].1.to_string();
                                }
                            });
                            ui.add_enabled_ui(self.keymap_idx == 4, |ui| {
                                ui.add(
                                    egui::TextEdit::singleline(&mut self.keymap_text)
                                        .desired_width(f32::INFINITY)
                                        .hint_text("w=1,s=4,space=-5"),
                                );
                                ui.label(
                                    RichText::new(
                                        "格式：源键=目标键，逗号分隔；字母直接写，\
                                         功能键 space/enter/esc",
                                    )
                                    .size(11.0)
                                    .color(SUB),
                                );
                            });
                            ui.add_space(2.0);
                            ui.checkbox(
                                &mut self.sync_jad,
                                RichText::new("转换后同步更新同目录 JAD").size(12.5),
                            );
                        });

                        Self::card(ui, "识别与风险", |ui| {
                            if self.info.is_empty() {
                                ui.label(
                                    RichText::new("尚未检查。选择 jar 后会自动检查。")
                                        .size(12.0)
                                        .color(SUB),
                                );
                            } else {
                                ui.label(
                                    RichText::new(&self.info).size(12.5).color(INK),
                                );
                                if !self.risks.is_empty() {
                                    ui.add_space(4.0);
                                    egui::ScrollArea::vertical()
                                        .max_height(120.0)
                                        .auto_shrink([false, false])
                                        .show(ui, |ui| {
                                            for r in self.risks.iter().take(40) {
                                                let col = if r.starts_with("警告")
                                                    || r.starts_with("不可上")
                                                    || r.starts_with("✘")
                                                {
                                                    BAD
                                                } else if r.starts_with("提示") {
                                                    WARN
                                                } else {
                                                    SUB
                                                };
                                                ui.add(
                                                    egui::Label::new(
                                                        RichText::new(r)
                                                            .size(11.0)
                                                            .color(col),
                                                    )
                                                    .wrap(),
                                                );
                                            }
                                        });
                                }
                            }
                        });

                        Self::card(ui, "操作", |ui| {
                            ui.horizontal_wrapped(|ui| {
                                if ui.add(Self::primary("转换 JAR")).clicked() {
                                    act = Some(Action::Convert);
                                }
                                if ui.add(Self::primary("一键构建 COD")).clicked() {
                                    act = Some(Action::BuildCod);
                                }
                            });
                            ui.add_space(4.0);
                            ui.horizontal_wrapped(|ui| {
                                if ui.add(Self::secondary("仅构建 COD")).clicked() {
                                    act = Some(Action::BuildOnly);
                                }
                                if ui.add(Self::secondary("装机（已有 COD）")).clicked() {
                                    act = Some(Action::Install);
                                }
                                if ui.add(Self::secondary("检测设备")).clicked() {
                                    self.check_device();
                                }
                            });
                            ui.add_space(4.0);
                            Self::field(ui, "COD 名称", |ui| {
                                ui.add(
                                    egui::TextEdit::singleline(&mut self.codname)
                                        .hint_text("留空按 jar 文件名生成")
                                        .desired_width(ui.available_width()),
                                );
                            });
                        });
                    });
            });

        egui::CentralPanel::default()
            .frame(egui::Frame {
                fill: BG,
                inner_margin: Margin::symmetric(0, 10),
                ..Default::default()
            })
            .show(ctx, |ui| {
                let frame = egui::Frame {
                    fill: CARD,
                    stroke: Stroke::new(1.0_f32, BORDER),
                    inner_margin: Margin::same(12),
                    corner_radius: egui::CornerRadius::same(8),
                    ..Default::default()
                };
                frame.show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("日志").size(13.5).strong().color(INK));
                        ui.with_layout(
                            egui::Layout::right_to_left(egui::Align::Center),
                            |ui| {
                                if ui.button("清空").clicked() {
                                    self.log.clear();
                                }
                            },
                        );
                    });
                    ui.add_space(4.0);
                    let h = ui.available_height();
                    egui::ScrollArea::vertical()
                        .stick_to_bottom(true)
                        .auto_shrink([false, false])
                        .max_height(h)
                        .show(ui, |ui| {
                            for line in &self.log {
                                let col = if line.contains("警告")
                                    || line.contains("失败")
                                    || line.starts_with("✘")
                                    || line.contains("错误")
                                {
                                    if line.contains("提示") {
                                        WARN
                                    } else {
                                        BAD
                                    }
                                } else if line.starts_with("✔")
                                    || line.contains("完成")
                                    || line.contains("就绪")
                                {
                                    GOOD
                                } else if line.starts_with("----")
                                    || line.starts_with("====")
                                    || line.starts_with("> ")
                                {
                                    SUB
                                } else {
                                    INK
                                };
                                ui.add(
                                    egui::Label::new(
                                        RichText::new(line).monospace().size(12.0).color(col),
                                    )
                                    .wrap(),
                                );
                            }
                        });
                });
            });
        act
    }

    fn ui_batch(&mut self, ctx: &egui::Context) -> Option<Action> {
        let mut act = None;
        egui::CentralPanel::default()
            .frame(egui::Frame {
                fill: BG,
                inner_margin: Margin::same(14),
                ..Default::default()
            })
            .show(ctx, |ui| {
                Self::card(ui, "批量扫描", |ui| {
                    Self::field(ui, "JAR 目录", |ui| {
                        ui.add(
                            egui::TextEdit::singleline(&mut self.batch_dir)
                                .desired_width(ui.available_width() - 62.0),
                        );
                        if ui.button("选择").clicked() {
                            act = Some(Action::BatchPickDir);
                        }
                    });
                    Self::field(ui, "输出目录", |ui| {
                        ui.add(
                            egui::TextEdit::singleline(&mut self.batch_out)
                                .desired_width(ui.available_width() - 62.0),
                        );
                        if ui.button("选择").clicked() {
                            act = Some(Action::BatchPickOut);
                        }
                    });
                    ui.horizontal_wrapped(|ui| {
                        ui.checkbox(
                            &mut self.recursive,
                            RichText::new("包含子目录").size(12.5),
                        );
                        ui.checkbox(
                            &mut self.force_bad,
                            RichText::new("强制转换“不可上”的包").size(12.5),
                        );
                        ui.add_space(8.0);
                        if ui.add(Self::secondary("开始扫描")).clicked() {
                            act = Some(Action::BatchScan);
                        }
                        if ui.add(Self::secondary("转换选中")).clicked() {
                            act = Some(Action::BatchSelected);
                        }
                        if ui.add(Self::primary("转换全部可上")).clicked() {
                            act = Some(Action::BatchAll);
                        }
                        if ui.button("全选/反选").clicked() {
                            if self.selected.len() == self.results.len() {
                                self.selected.clear();
                            } else {
                                self.selected = (0..self.results.len()).collect();
                            }
                        }
                    });
                    ui.add_space(4.0);
                    if self.batch_busy || self.batch_total > 0 {
                        let frac = if self.batch_total > 0 {
                            self.batch_done as f32 / self.batch_total as f32
                        } else {
                            0.0
                        };
                        let text = if self.batch_status.is_empty() {
                            format!("{}/{}", self.batch_done, self.batch_total)
                        } else {
                            self.batch_status.clone()
                        };
                        ui.add(
                            egui::ProgressBar::new(frac)
                                .text(text)
                                .desired_height(16.0),
                        );
                    }
                    if !self.report.is_empty() {
                        ui.label(
                            RichText::new(format!("失败报告: {}", self.report))
                                .size(11.5)
                                .color(SUB),
                        );
                    }
                });

                let frame = egui::Frame {
                    fill: CARD,
                    stroke: Stroke::new(1.0_f32, BORDER),
                    inner_margin: Margin::same(8),
                    corner_radius: egui::CornerRadius::same(8),
                    ..Default::default()
                };
                frame.show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.add_sized([22.0, 20.0], egui::Label::new(""));
                        ui.add_sized(
                            [58.0, 20.0],
                            egui::Label::new(RichText::new("状态").size(12.0).color(SUB)),
                        );
                        ui.add_sized(
                            [280.0, 20.0],
                            egui::Label::new(RichText::new("文件").size(12.0).color(SUB)),
                        );
                        ui.add_sized(
                            [92.0, 20.0],
                            egui::Label::new(RichText::new("分辨率").size(12.0).color(SUB)),
                        );
                        ui.add_sized(
                            [62.0, 20.0],
                            egui::Label::new(RichText::new("按键").size(12.0).color(SUB)),
                        );
                        ui.label(RichText::new("说明").size(12.0).color(SUB));
                    });
                    ui.separator();
                    let avail = (ui.available_height() - 6.0).max(60.0);
                    egui::ScrollArea::vertical()
                        .auto_shrink([false, false])
                        .max_height(avail)
                        .show_rows(ui, 23.0, self.results.len(), |ui, range| {
                            for i in range {
                                let r = &self.results[i];
                                ui.horizontal(|ui| {
                                    let mut sel = self.selected.contains(&i);
                                    if ui.add_sized(
                                        [22.0, 20.0],
                                        egui::Checkbox::new(&mut sel, ""),
                                    ).changed() {
                                        if sel {
                                            self.selected.insert(i);
                                        } else {
                                            self.selected.remove(&i);
                                        }
                                    }
                                    let (txt, col) = if r.blackberry_ok {
                                        ("可上", GOOD)
                                    } else {
                                        ("不可上", BAD)
                                    };
                                    ui.add_sized(
                                        [58.0, 20.0],
                                        egui::Label::new(
                                            RichText::new(txt).size(11.5).color(col),
                                        ),
                                    );
                                    ui.add_sized(
                                        [280.0, 20.0],
                                        egui::Label::new(
                                            RichText::new(&r.name).size(12.0).color(INK),
                                        )
                                        .truncate(),
                                    );
                                    let size = r
                                        .native_size_guess
                                        .map(|(w, h)| format!("{w}x{h}"))
                                        .unwrap_or_else(|| "?".into());
                                    ui.add_sized(
                                        [92.0, 20.0],
                                        egui::Label::new(
                                            RichText::new(size).size(11.5).color(SUB),
                                        ),
                                    );
                                    let km = if r.vendor_refs.is_empty() {
                                        "数字"
                                    } else {
                                        "厂商"
                                    };
                                    ui.add_sized(
                                        [62.0, 20.0],
                                        egui::Label::new(
                                            RichText::new(km).size(11.0).color(SUB),
                                        ),
                                    );
                                    let why = if r.reason.is_empty() {
                                        r.integrity_warn.join("；")
                                    } else {
                                        r.reason.clone()
                                    };
                                    ui.add(
                                        egui::Label::new(
                                            RichText::new(why)
                                                .size(11.0)
                                                .color(if r.blackberry_ok { SUB } else { BAD }),
                                        )
                                        .truncate(),
                                    );
                                });
                            }
                        });
                });
            });
        act
    }

    fn ui_help(&mut self, ctx: &egui::Context) -> Option<Action> {
        egui::CentralPanel::default()
            .frame(egui::Frame {
                fill: BG,
                inner_margin: Margin::same(14),
                ..Default::default()
            })
            .show(ctx, |ui| {
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        let frame = egui::Frame {
                            fill: CARD,
                            stroke: Stroke::new(1.0_f32, BORDER),
                            inner_margin: Margin::same(16),
                            corner_radius: egui::CornerRadius::same(8),
                            ..Default::default()
                        };
                        frame.show(ui, |ui| {
                            ui.set_width(ui.available_width());
                            ui.label(
                                RichText::new("JarResizer 2.0")
                                    .size(20.0)
                                    .strong()
                                    .color(INK),
                            );
                            ui.label(
                                RichText::new(
                                    "把诺基亚 360×640 的 J2ME 游戏转换到 BlackBerry 9930 / OS 7.1。",
                                )
                                .size(12.5)
                                .color(SUB),
                            );
                            ui.add_space(10.0);
                            for (h, lines) in [
                                (
                                    "使用步骤",
                                    vec![
                                        "1. 选择游戏 JAR，点“快速检查”查看分辨率、键位与风险。",
                                        "2. 需要微调时改键位、铺满策略或原生分辨率。",
                                        "3. 点“转换 JAR”得到可直接分发的适配包。",
                                        "4. 或点“一键构建 COD”，自动预验证、生成 JAD、rapc 编译并尝试装机。",
                                    ],
                                ),
                                (
                                    "两种画面模式",
                                    vec![
                                        "自动适配：注入 jr.ScaleCanvas，游戏在原生尺寸渲染，再缩放到目标屏幕；兼容性最好。",
                                        "固定模式：直接改写游戏字节码中的 BIPUSH/SIPUSH 尺寸常量；画面最清晰，但只适用于常量式布局。",
                                    ],
                                ),
                                (
                                    "按键方案",
                                    vec![
                                        "自动识别：统计 keyPressed 链路中的负厂商码与 ASCII 数字键。",
                                        "数字键：WER/SDF/ZXC 映射到 1-9，空格与触摸板按压映射到 5。",
                                        "厂商码：WASD 映射到 -1..-4，空格映射到 -5。",
                                    ],
                                ),
                                (
                                    "限制",
                                    vec![
                                        "• 单个 COD 代码段 64KB，超大类会自动拆分。",
                                        "• 每个 COD 最多 255 个分片（约 20MB），序值 255 保留给根模块。",
                                        "• 重度混淆（超大验证帧）的游戏可能无法通过 rapc，属平台限制。",
                                        "• 转换只处理 class 与资源，不修改游戏逻辑。",
                                    ],
                                ),
                                (
                                    "目录",
                                    vec![
                                        "assets/：预验证 class 与资源（运行时读取，请勿删）。",
                                        "tools/：自带 JRE、ProGuard、rapc、javaloader。",
                                        "adapter/、filestub/、jsecstub/、nokiashim/、sensorstub/、wmastub/、patches/：stub 源码与构建脚本。",
                                        "bbbuild/：COD 构建中间产物。",
                                    ],
                                ),
                            ] {
                                ui.add_space(8.0);
                                ui.label(RichText::new(h).size(14.0).strong().color(ACCENT));
                                for l in lines {
                                    ui.add(
                                        egui::Label::new(
                                            RichText::new(l).size(12.5).color(INK),
                                        )
                                        .wrap(),
                                    );
                                }
                            }
                            ui.add_space(14.0);
                            ui.label(
                                RichText::new("JarResizer 2.0 · MIT License")
                                    .size(11.0)
                                    .color(SUB),
                            );
                        });
                    });
            });
        None
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.drain();
        egui::TopBottomPanel::top("top")
            .frame(egui::Frame {
                fill: CARD,
                stroke: Stroke::NONE,
                inner_margin: Margin::symmetric(16, 10),
                ..Default::default()
            })
            .show(ctx, |ui| self.ui_top(ui));

        egui::TopBottomPanel::bottom("status")
            .frame(egui::Frame {
                fill: CARD,
                inner_margin: Margin::symmetric(16, 6),
                ..Default::default()
            })
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    let col = if self.busy { ACCENT } else { SUB };
                    ui.label(RichText::new(&self.status).size(11.5).color(col));
                    ui.with_layout(
                        egui::Layout::right_to_left(egui::Align::Center),
                        |ui| {
                            ui.label(
                                RichText::new("assets/ + tools/ 必须与程序同目录保留")
                                    .size(10.5)
                                    .color(SUB),
                            );
                        },
                    );
                });
            });

        let act = match self.tab {
            Tab::Convert => self.ui_convert(ctx),
            Tab::Batch => self.ui_batch(ctx),
            Tab::Help => {
                self.ui_help(ctx);
                None
            }
        };
        if let Some(a) = act {
            self.handle(a);
        }
    }
}

fn install_fonts(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    for path in [
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
    ] {
        if let Ok(data) = std::fs::read(path) {
            fonts.font_data.insert(
                "cjk".to_owned(),
                std::sync::Arc::new(egui::FontData::from_owned(data)),
            );
            for fam in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
                fonts.families.entry(fam).or_default().push("cjk".to_owned());
            }
            break;
        }
    }
    ctx.set_fonts(fonts);
}

fn apply_style(ctx: &egui::Context) {
    let mut visuals = egui::Visuals::light();
    visuals.panel_fill = BG;
    visuals.window_fill = CARD;
    visuals.extreme_bg_color = CARD;
    visuals.faint_bg_color = Color32::from_rgb(248, 250, 253);
    visuals.override_text_color = Some(INK);
    visuals.selection.bg_fill = ACCENT;
    visuals.selection.stroke = Stroke::new(1.0_f32, Color32::WHITE);
    visuals.hyperlink_color = ACCENT;
    visuals.widgets.noninteractive.bg_fill = CARD;
    visuals.widgets.noninteractive.fg_stroke = Stroke::new(1.0_f32, INK);
    visuals.widgets.inactive.bg_fill = Color32::from_rgb(247, 249, 252);
    visuals.widgets.inactive.fg_stroke = Stroke::new(1.0_f32, INK);
    visuals.widgets.inactive.bg_stroke = Stroke::new(1.0_f32, BORDER);
    visuals.widgets.inactive.corner_radius = egui::CornerRadius::same(5);
    visuals.widgets.hovered.bg_fill = Color32::from_rgb(232, 239, 253);
    visuals.widgets.hovered.bg_stroke = Stroke::new(1.0_f32, ACCENT);
    visuals.widgets.hovered.fg_stroke = Stroke::new(1.0_f32, INK);
    visuals.widgets.hovered.corner_radius = egui::CornerRadius::same(5);
    visuals.widgets.active.bg_fill = Color32::from_rgb(219, 230, 251);
    visuals.widgets.active.bg_stroke = Stroke::new(1.0_f32, ACCENT);
    visuals.widgets.active.fg_stroke = Stroke::new(1.0_f32, INK);
    visuals.widgets.active.corner_radius = egui::CornerRadius::same(5);
    let mut style = (*ctx.style()).clone();
    style.visuals = visuals;
    style.spacing.item_spacing = egui::vec2(8.0, 7.0);
    style.spacing.button_padding = egui::vec2(11.0, 5.0);
    style.spacing.interact_size.y = 24.0;
    ctx.set_style(style);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keymap_roundtrip() {
        let v = parse_keymap("w=1,s=4,space=-5,-8=5,enter=-6").unwrap();
        assert_eq!(v, vec![119, 1, 115, 4, 32, -5, -8, 5, 10, -6]);
    }

    #[test]
    fn keymap_errors() {
        assert!(parse_keymap("w").is_err());
        assert!(parse_keymap("nokey=1").is_err());
    }
}
