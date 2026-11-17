//! Java class 文件解析 / 回写（纯 Rust，不依赖 JVM 库）。
//!
//! 只保留转换器真正用到的结构：常量池、字段/方法、属性。常量池用
//! `Vec<Option<CpEntry>>` 表示，下标与 class 文件一致；long/double 占
//! 两个槽位（第二个为 None），回写时跳过，长度按槽位总数写回。

#[derive(Clone, Debug)]
pub enum CpEntry {
    Utf8(String),
    /// int / float / long / double（原始 4 或 8 字节，大端）
    Raw { tag: u8, bytes: Vec<u8> },
    Class(u16),
    String(u16),
    /// Fieldref / Methodref / InterfaceMethodref / NameAndType /
    /// Dynamic / InvokeDynamic
    Ref { tag: u8, a: u16, b: u16 },
    MethodHandle(u8, u16),
    MethodType(u16),
    Module(u16),
    Package(u16),
}

impl CpEntry {
    pub fn tag(&self) -> u8 {
        match self {
            CpEntry::Utf8(_) => 1,
            CpEntry::Raw { tag, .. } => *tag,
            CpEntry::Class(_) => 7,
            CpEntry::String(_) => 8,
            CpEntry::Ref { tag, .. } => *tag,
            CpEntry::MethodHandle(..) => 15,
            CpEntry::MethodType(_) => 16,
            CpEntry::Module(_) => 19,
            CpEntry::Package(_) => 20,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Attr {
    pub name: u16,
    pub data: Vec<u8>,
}

#[derive(Clone, Debug)]
pub struct Member {
    pub access: u16,
    pub name: u16,
    pub desc: u16,
    pub attrs: Vec<Attr>,
}

#[derive(Clone, Debug)]
pub struct ClassFile {
    pub minor: u16,
    pub major: u16,
    pub cp: Vec<Option<CpEntry>>,
    pub access: u16,
    pub this_c: u16,
    pub super_c: u16,
    pub interfaces: Vec<u16>,
    pub fields: Vec<Member>,
    pub methods: Vec<Member>,
    pub attrs: Vec<Attr>,
}

pub type CResult<T> = Result<T, String>;

struct Reader<'a> {
    d: &'a [u8],
    p: usize,
}

impl<'a> Reader<'a> {
    fn new(d: &'a [u8]) -> Self {
        Reader { d, p: 0 }
    }
    fn need(&self, n: usize) -> CResult<()> {
        if self.p + n > self.d.len() {
            Err("class 文件截断".into())
        } else {
            Ok(())
        }
    }
    fn u1(&mut self) -> CResult<u8> {
        self.need(1)?;
        let v = self.d[self.p];
        self.p += 1;
        Ok(v)
    }
    fn u2(&mut self) -> CResult<u16> {
        self.need(2)?;
        let v = u16::from_be_bytes([self.d[self.p], self.d[self.p + 1]]);
        self.p += 2;
        Ok(v)
    }
    fn u4(&mut self) -> CResult<u32> {
        self.need(4)?;
        let v = u32::from_be_bytes([
            self.d[self.p],
            self.d[self.p + 1],
            self.d[self.p + 2],
            self.d[self.p + 3],
        ]);
        self.p += 4;
        Ok(v)
    }
    fn bytes(&mut self, n: usize) -> CResult<Vec<u8>> {
        self.need(n)?;
        let v = self.d[self.p..self.p + n].to_vec();
        self.p += n;
        Ok(v)
    }
}

impl ClassFile {
    pub fn parse(data: &[u8]) -> CResult<ClassFile> {
        if data.len() < 10 || &data[..4] != b"\xca\xfe\xba\xbe" {
            return Err("bad class magic".into());
        }
        let mut r = Reader::new(data);
        r.p = 4;
        let minor = r.u2()?;
        let major = r.u2()?;
        let n = r.u2()? as usize;
        let mut cp: Vec<Option<CpEntry>> = vec![None];
        let mut idx = 1usize;
        while idx < n {
            let tag = r.u1()?;
            let e = match tag {
                1 => {
                    let ln = r.u2()? as usize;
                    let b = r.bytes(ln)?;
                    CpEntry::Utf8(String::from_utf8_lossy(&b).into_owned())
                }
                3 | 4 => CpEntry::Raw {
                    tag,
                    bytes: r.bytes(4)?,
                },
                5 | 6 => CpEntry::Raw {
                    tag,
                    bytes: r.bytes(8)?,
                },
                7 => CpEntry::Class(r.u2()?),
                8 => CpEntry::String(r.u2()?),
                9 | 10 | 11 | 12 | 17 | 18 => {
                    let a = r.u2()?;
                    let b = r.u2()?;
                    CpEntry::Ref { tag, a, b }
                }
                15 => {
                    let kind = r.u1()?;
                    let i = r.u2()?;
                    CpEntry::MethodHandle(kind, i)
                }
                16 => CpEntry::MethodType(r.u2()?),
                19 => CpEntry::Module(r.u2()?),
                20 => CpEntry::Package(r.u2()?),
                _ => return Err(format!("unsupported cp tag {tag}")),
            };
            cp.push(Some(e));
            if tag == 5 || tag == 6 {
                cp.push(None); // two-slot entry
                idx += 1;
            }
            idx += 1;
        }
        let access = r.u2()?;
        let this_c = r.u2()?;
        let super_c = r.u2()?;
        let ic = r.u2()? as usize;
        let mut interfaces = Vec::with_capacity(ic);
        for _ in 0..ic {
            interfaces.push(r.u2()?);
        }
        let (fields, methods, attrs) = {
            let (fields, p) = Self::parse_members(data, r.p)?;
            let (methods, p) = Self::parse_members(data, p)?;
            let (attrs, _) = Self::parse_attrs(data, p)?;
            (fields, methods, attrs)
        };
        Ok(ClassFile {
            minor,
            major,
            cp,
            access,
            this_c,
            super_c,
            interfaces,
            fields,
            methods,
            attrs,
        })
    }

    fn parse_members(d: &[u8], mut p: usize) -> CResult<(Vec<Member>, usize)> {
        let mut r = Reader::new(d);
        r.p = p;
        let mc = r.u2()? as usize;
        let mut out = Vec::with_capacity(mc);
        for _ in 0..mc {
            let access = r.u2()?;
            let name = r.u2()?;
            let desc = r.u2()?;
            let (attrs, np) = Self::parse_attrs(d, r.p)?;
            r.p = np;
            out.push(Member {
                access,
                name,
                desc,
                attrs,
            });
        }
        p = r.p;
        Ok((out, p))
    }

    fn parse_attrs(d: &[u8], mut p: usize) -> CResult<(Vec<Attr>, usize)> {
        let mut r = Reader::new(d);
        r.p = p;
        let ac = r.u2()? as usize;
        let mut out = Vec::with_capacity(ac);
        for _ in 0..ac {
            let name = r.u2()?;
            let ln = r.u4()? as usize;
            let data = r.bytes(ln)?;
            out.push(Attr { name, data });
        }
        p = r.p;
        Ok((out, p))
    }

    // ---------------------------------------------------------- cp helpers
    pub fn utf8(&self, idx: u16) -> Option<&str> {
        match self.cp.get(idx as usize) {
            Some(Some(CpEntry::Utf8(s))) => Some(s.as_str()),
            _ => None,
        }
    }

    pub fn utf8s(&self, idx: u16) -> String {
        self.utf8(idx).unwrap_or("").to_string()
    }

    pub fn class_name(&self, idx: u16) -> Option<String> {
        match self.cp.get(idx as usize) {
            Some(Some(CpEntry::Class(n))) => self.utf8(*n).map(|s| s.to_string()),
            _ => None,
        }
    }

    pub fn add_utf8(&mut self, s: &str) -> u16 {
        for (i, e) in self.cp.iter().enumerate() {
            if let Some(CpEntry::Utf8(x)) = e {
                if x == s {
                    return i as u16;
                }
            }
        }
        self.cp.push(Some(CpEntry::Utf8(s.to_string())));
        (self.cp.len() - 1) as u16
    }

    pub fn add_class(&mut self, name: &str) -> u16 {
        for i in 1..self.cp.len() {
            if let Some(Some(CpEntry::Class(n))) = self.cp.get(i) {
                if self.utf8(*n) == Some(name) {
                    return i as u16;
                }
            }
        }
        let ni = self.add_utf8(name);
        self.cp.push(Some(CpEntry::Class(ni)));
        (self.cp.len() - 1) as u16
    }

    pub fn add_nat(&mut self, name: &str, desc: &str) -> u16 {
        let ni = self.add_utf8(name);
        let di = self.add_utf8(desc);
        for i in 1..self.cp.len() {
            if let Some(Some(CpEntry::Ref { tag: 12, a, b })) = self.cp.get(i) {
                if *a == ni && *b == di {
                    return i as u16;
                }
            }
        }
        self.cp.push(Some(CpEntry::Ref {
            tag: 12,
            a: ni,
            b: di,
        }));
        (self.cp.len() - 1) as u16
    }

    pub fn this_name(&self) -> Option<String> {
        self.class_name(self.this_c)
    }

    pub fn super_name(&self) -> Option<String> {
        self.class_name(self.super_c)
    }

    /// 指向 Code 属性的引用 (method_index, attr_index)，供扫描使用。
    pub fn code_refs(&self) -> Vec<(usize, usize)> {
        let mut out = Vec::new();
        for (mi, m) in self.methods.iter().enumerate() {
            for (ai, a) in m.attrs.iter().enumerate() {
                if self.utf8(a.name) == Some("Code") {
                    out.push((mi, ai));
                }
            }
        }
        out
    }

    /// 取出一个 Code 属性里的字节码（已复制）。
    pub fn code_of(&self, mi: usize, ai: usize) -> Option<Vec<u8>> {
        let blob = &self.methods.get(mi)?.attrs.get(ai)?.data;
        code_from_blob(blob).map(|(code, _)| code)
    }

    pub fn set_code(&mut self, mi: usize, ai: usize, code: &[u8]) {
        if let Some(a) = self
            .methods
            .get_mut(mi)
            .and_then(|m| m.attrs.get_mut(ai))
        {
            let blob = &a.data;
            let mut out = Vec::with_capacity(blob.len());
            out.extend_from_slice(&blob[..8.min(blob.len())]);
            out.extend_from_slice(code);
            let rest = 8 + code.len();
            if rest < blob.len() {
                // 原属性里 code 之后的异常表/子属性原样保留
                if let Some((_, suffix_start)) = code_from_blob(blob) {
                    if suffix_start < blob.len() {
                        out.extend_from_slice(&blob[suffix_start..]);
                    }
                }
            }
            a.data = out;
        }
    }

    /// 遍历所有方法的 Code 字节码（复制，便于同时改常量池）。
    pub fn code_blobs(&self) -> Vec<Vec<u8>> {
        let mut out = Vec::new();
        for (mi, ai) in self.code_refs() {
            if let Some(c) = self.code_of(mi, ai) {
                out.push(c);
            }
        }
        out
    }

    // ------------------------------------------------------------- emit
    pub fn build(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(4096);
        out.extend_from_slice(b"\xca\xfe\xba\xbe");
        out.extend_from_slice(&self.minor.to_be_bytes());
        out.extend_from_slice(&self.major.to_be_bytes());
        out.extend_from_slice(&(self.cp.len() as u16).to_be_bytes());
        for e in self.cp.iter().skip(1) {
            let e = match e {
                Some(e) => e,
                None => continue,
            };
            let tag = e.tag();
            out.push(tag);
            match e {
                CpEntry::Utf8(s) => {
                    let b = s.as_bytes();
                    out.extend_from_slice(&(b.len() as u16).to_be_bytes());
                    out.extend_from_slice(b);
                }
                CpEntry::Raw { bytes, .. } => out.extend_from_slice(bytes),
                CpEntry::Class(i) | CpEntry::String(i) | CpEntry::MethodType(i)
                | CpEntry::Module(i) | CpEntry::Package(i) => {
                    out.extend_from_slice(&i.to_be_bytes())
                }
                CpEntry::Ref { a, b, .. } => {
                    out.extend_from_slice(&a.to_be_bytes());
                    out.extend_from_slice(&b.to_be_bytes());
                }
                CpEntry::MethodHandle(kind, i) => {
                    out.push(*kind);
                    out.extend_from_slice(&i.to_be_bytes());
                }
            }
        }
        out.extend_from_slice(&self.access.to_be_bytes());
        out.extend_from_slice(&self.this_c.to_be_bytes());
        out.extend_from_slice(&self.super_c.to_be_bytes());
        out.extend_from_slice(&(self.interfaces.len() as u16).to_be_bytes());
        for x in &self.interfaces {
            out.extend_from_slice(&x.to_be_bytes());
        }
        emit_members(&mut out, &self.fields);
        emit_members(&mut out, &self.methods);
        emit_attrs(&mut out, &self.attrs);
        out
    }
}

fn emit_members(out: &mut Vec<u8>, members: &[Member]) {
    out.extend_from_slice(&(members.len() as u16).to_be_bytes());
    for m in members {
        out.extend_from_slice(&m.access.to_be_bytes());
        out.extend_from_slice(&m.name.to_be_bytes());
        out.extend_from_slice(&m.desc.to_be_bytes());
        emit_attrs(out, &m.attrs);
    }
}

fn emit_attrs(out: &mut Vec<u8>, attrs: &[Attr]) {
    out.extend_from_slice(&(attrs.len() as u16).to_be_bytes());
    for a in attrs {
        out.extend_from_slice(&a.name.to_be_bytes());
        out.extend_from_slice(&(a.data.len() as u32).to_be_bytes());
        out.extend_from_slice(&a.data);
    }
}

/// Code 属性: u2 max_stack, u2 max_locals, u4 code_len, code...
/// 返回 (code, code 结束偏移)。
pub fn code_from_blob(blob: &[u8]) -> Option<(Vec<u8>, usize)> {
    if blob.len() < 8 {
        return None;
    }
    let len = u32::from_be_bytes([blob[4], blob[5], blob[6], blob[7]]) as usize;
    if 8 + len > blob.len() {
        return None;
    }
    Some((blob[8..8 + len].to_vec(), 8 + len))
}
