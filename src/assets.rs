//! 读取 `assets/` 下的预编译 class / 资源。目录保持原样随仓库分发。

use std::path::PathBuf;

pub fn asset_bytes(rel: &str) -> Result<Vec<u8>, String> {
    let p: PathBuf = crate::tools::root().join("assets").join(rel);
    std::fs::read(&p).map_err(|e| format!("缺少 assets/{rel}（先运行对应 stub 的 build 脚本）: {e}"))
}

pub fn scale_canvas() -> Result<Vec<u8>, String> {
    asset_bytes("jr/ScaleCanvas.class")
}
