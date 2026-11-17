//! jar(zip) 读写。读取时保留条目顺序；非 UTF-8 标记的历史文件名按
//! GBK 解码（国内打包器常用 GBK 写中文资源名）。写出统一 DEFLATED。

use std::fs::File;
use std::io::{Read, Write};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipArchive, ZipWriter};

pub struct JarEntry {
    pub name: String,
    pub data: Vec<u8>,
}

pub struct Jar {
    pub entries: Vec<JarEntry>,
}

fn decode_name(raw: &[u8]) -> String {
    if let Ok(s) = std::str::from_utf8(raw) {
        return s.to_string();
    }
    let (cow, _, _) = encoding_rs::GBK.decode(raw);
    cow.into_owned()
}

impl Jar {
    pub fn read(path: &str) -> Result<Jar, String> {
        let f = File::open(path).map_err(|e| format!("无法打开 jar: {e}"))?;
        let mut z = ZipArchive::new(f).map_err(|e| format!("jar 不是有效 zip: {e}"))?;
        let mut entries = Vec::with_capacity(z.len());
        for i in 0..z.len() {
            let mut e = z.by_index(i).map_err(|e| format!("读取 zip 条目失败: {e}"))?;
            let name = decode_name(e.name_raw());
            if e.is_dir() {
                entries.push(JarEntry {
                    name,
                    data: Vec::new(),
                });
                continue;
            }
            let mut data = Vec::with_capacity(e.size() as usize);
            e.read_to_end(&mut data)
                .map_err(|ex| format!("解压 {name} 失败: {ex}"))?;
            entries.push(JarEntry { name, data });
        }
        Ok(Jar { entries })
    }

    pub fn names(&self) -> Vec<String> {
        self.entries.iter().map(|e| e.name.clone()).collect()
    }

    pub fn get(&self, name: &str) -> Option<&[u8]> {
        self.entries
            .iter()
            .find(|e| e.name == name)
            .map(|e| e.data.as_slice())
    }
}

pub fn write_jar(path: &str, entries: &[(String, Vec<u8>)]) -> Result<(), String> {
    let f = File::create(path).map_err(|e| format!("创建输出 jar 失败: {e}"))?;
    let mut zw = ZipWriter::new(f);
    let opts = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    for (name, data) in entries {
        if name.ends_with('/') {
            zw.add_directory(name.trim_end_matches('/'), opts)
                .map_err(|e| format!("写入目录 {name} 失败: {e}"))?;
            continue;
        }
        zw.start_file(name.as_str(), opts)
            .map_err(|e| format!("写入 {name} 失败: {e}"))?;
        zw.write_all(data)
            .map_err(|e| format!("写入 {name} 失败: {e}"))?;
    }
    zw.finish().map_err(|e| format!("结束 zip 失败: {e}"))?;
    Ok(())
}
