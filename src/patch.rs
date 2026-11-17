//! AUTO 模式改写（注入 ScaleCanvas）与 FIXED 模式常量补丁。

use std::collections::{HashMap, HashSet};

use crate::bytecode::{push_value, walk_code};
use crate::classfile::{ClassFile, CpEntry};
use crate::consts::*;

fn set_u16(code: &mut [u8], pos: usize, v: u16) {
    if pos + 2 <= code.len() {
        code[pos] = (v >> 8) as u8;
        code[pos + 1] = v as u8;
    }
}

pub fn fits_bipush(v: i32) -> bool {
    (-128..=127).contains(&v)
}

pub fn fits_sipush(v: i32) -> bool {
    (-32768..=32767).contains(&v)
}

/// AUTO：把 Canvas/GameCanvas 子类改挂到 jr.ScaleCanvas，并重命名
/// 会被适配层 final 方法拦截的回调（paint/keyPressed/...）。
/// 返回 None 表示该类不在转换集合内。
pub fn transform_auto(
    data: &[u8],
    transformed: &HashSet<String>,
) -> Result<Option<Vec<u8>>, String> {
    let mut cf = ClassFile::parse(data)?;
    let name = match cf.this_name() {
        Some(n) => n,
        None => return Ok(None),
    };
    if !transformed.contains(&name) {
        return Ok(None);
    }
    let adapter_class = cf.add_class(ADAPTER);

    // 1) 重命名已声明的方法，避免与适配层 final 方法冲突
    for mi in 0..cf.methods.len() {
        let nm = cf.utf8s(cf.methods[mi].name);
        let ds = cf.utf8s(cf.methods[mi].desc);
        if let Some((_, _, new)) = RENAME_METHODS
            .iter()
            .find(|(n, d, _)| *n == nm && *d == ds)
        {
            let ni = cf.add_utf8(new);
            cf.methods[mi].name = ni;
        }
    }

    // 2) 统计每个方法引用被 invokespecial / invokevirtual 使用的情况
    let mut used_special: HashSet<u16> = HashSet::new();
    let mut used_virtual: HashSet<u16> = HashSet::new();
    for (mi, ai) in cf.code_refs() {
        let code = match cf.code_of(mi, ai) {
            Some(c) => c,
            None => continue,
        };
        for ins in walk_code(&code) {
            if ins.operand.len() < 2 {
                continue;
            }
            let idx = u16::from_be_bytes([ins.operand[0], ins.operand[1]]);
            match ins.op {
                0xb7 => {
                    used_special.insert(idx);
                }
                0xb6 | 0xb9 => {
                    used_virtual.insert(idx);
                }
                _ => {}
            }
        }
    }

    let mut code_patches: HashMap<u16, u16> = HashMap::new();
    let mut pending_cp: Vec<CpEntry> = Vec::new();
    let mut gc_init_refs: HashSet<u16> = HashSet::new();

    // 3) 改写常量池中的方法/构造器引用
    let cp_len = cf.cp.len();
    for ci in 1..cp_len {
        let mut e = match cf.cp.get(ci).cloned().flatten() {
            Some(CpEntry::Ref { tag, a, b }) if tag == 10 || tag == 11 => {
                CpEntry::Ref { tag, a, b }
            }
            _ => continue,
        };
        // 安全解包
        let (tag, a, b) = match &e {
            CpEntry::Ref { tag, a, b } => (*tag, *a, *b),
            _ => continue,
        };
        let owner = match cf.class_name(a) {
            Some(o) => o,
            None => continue,
        };
        let (nm, ds) = match cf.cp.get(b as usize).cloned().flatten() {
            Some(CpEntry::Ref { tag: 12, a: ni, b: di }) => {
                (cf.utf8s(ni), cf.utf8s(di))
            }
            _ => continue,
        };
        let is_canvas = owner == CANVAS || owner == NOKIA_FULLCANVAS;

        if is_canvas && nm == "<init>" && ds == "()V" {
            e = CpEntry::Ref { tag, a: adapter_class, b };
            cf.cp[ci] = Some(e);
            continue;
        }
        if owner == GAMECANVAS && nm == "<init>" && ds == "(Z)V" {
            // super(boolean) -> 适配层无参构造；压栈的 iconst 在指令层 NOP
            gc_init_refs.insert(ci as u16);
            let nati = cf.add_nat("<init>", "()V");
            e = CpEntry::Ref {
                tag,
                a: adapter_class,
                b: nati,
            };
            cf.cp[ci] = Some(e);
            continue;
        }
        if transformed.contains(&owner) {
            if let Some((_, _, new)) = RENAME_REF_RULES
                .iter()
                .find(|(n, d, _)| *n == nm && *d == ds)
            {
                let nati = cf.add_nat(new, &ds);
                e = CpEntry::Ref { tag, a, b: nati };
                cf.cp[ci] = Some(e);
            }
            continue;
        }
        if is_canvas
            && SUPER_RETARGET.iter().any(|(n, d)| *n == nm && *d == ds)
            && used_special.contains(&(ci as u16))
        {
            let new = RENAME_REF_RULES
                .iter()
                .find(|(n, d, _)| *n == nm && *d == ds)
                .map(|(_, _, x)| *x)
                .unwrap_or("");
            if used_virtual.contains(&(ci as u16)) {
                // 同一 CP 条目同时被超类调用和虚调用共享：保留原条目，
                // 另建一条指向适配层钩子的 Methodref 给 invokespecial。
                let nati = cf.add_nat(new, &ds);
                pending_cp.push(CpEntry::Ref {
                    tag: 10,
                    a: adapter_class,
                    b: nati,
                });
                let ni = cf.cp.len() + pending_cp.len() - 1;
                code_patches.insert(ci as u16, ni as u16);
            } else {
                let nati = cf.add_nat(new, &ds);
                e = CpEntry::Ref {
                    tag,
                    a: adapter_class,
                    b: nati,
                };
                cf.cp[ci] = Some(e);
            }
        }
    }
    for p in pending_cp {
        cf.cp.push(Some(p));
    }

    // 4) 指令级重定向 + GameCanvas 布尔参数移除
    if !code_patches.is_empty() || !gc_init_refs.is_empty() {
        for (mi, ai) in cf.code_refs() {
            let mut code = match cf.code_of(mi, ai) {
                Some(c) => c,
                None => continue,
            };
            let mut changed = false;
            let mut prev: i64 = -10;
            for ins in walk_code(&code) {
                if ins.op == 0xb7 && ins.operand.len() >= 2 {
                    let oldi = u16::from_be_bytes([ins.operand[0], ins.operand[1]]);
                    if let Some(newi) = code_patches.get(&oldi) {
                        set_u16(&mut code, ins.off + 1, *newi);
                        changed = true;
                    } else if gc_init_refs.contains(&oldi)
                        && ins.off as i64 - prev == 1
                        && prev >= 0
                        && matches!(code[prev as usize], 0x03 | 0x04)
                    {
                        // iconst_0/1 压入 suppressKeyEvents 标志 -> nop
                        code[prev as usize] = 0x00;
                        changed = true;
                    }
                }
                prev = ins.off as i64;
            }
            if changed {
                cf.set_code(mi, ai, &code);
            }
        }
    }

    // 5) 直接继承 Canvas / FullCanvas / GameCanvas 的类换父类
    if let Some(sup) = cf.super_name() {
        if sup == CANVAS || sup == NOKIA_FULLCANVAS || sup == GAMECANVAS {
            cf.super_c = adapter_class;
        }
    }

    Ok(Some(cf.build()))
}

/// FIXED：同长度替换 BIPUSH/SIPUSH 中的尺寸常量。
/// 返回 (改写后的 class, 替换数量)；没有命中时返回 (None, 0)。
pub fn patch_constants(
    data: &[u8],
    replacements: &HashMap<i32, i32>,
) -> Result<(Option<Vec<u8>>, usize), String> {
    let mut cf = ClassFile::parse(data)?;
    let mut applied = 0usize;
    for (mi, ai) in cf.code_refs() {
        let mut code = match cf.code_of(mi, ai) {
            Some(c) => c,
            None => continue,
        };
        let mut changed = false;
        for ins in walk_code(&code) {
            if ins.op != 0x10 && ins.op != 0x11 {
                continue;
            }
            let v = match push_value(ins.op, &ins.operand) {
                Some(v) => v,
                None => continue,
            };
            if let Some(&nv) = replacements.get(&v) {
                if ins.op == 0x10 {
                    if !fits_bipush(nv) {
                        return Err(format!("常量 {v}→{nv} 无法同长度替换(BIPUSH)"));
                    }
                    code[ins.off + 1] = nv as i8 as u8;
                } else {
                    if !fits_sipush(nv) {
                        return Err(format!("常量 {v}→{nv} 超出 SIPUSH 范围"));
                    }
                    let b = (nv as i16).to_be_bytes();
                    code[ins.off + 1] = b[0];
                    code[ins.off + 2] = b[1];
                }
                changed = true;
                applied += 1;
            }
        }
        if changed {
            cf.set_code(mi, ai, &code);
        }
    }
    if applied > 0 {
        Ok((Some(cf.build()), applied))
    } else {
        Ok((None, 0))
    }
}

/// 重新生成 Code 属性体，去掉行号/局部变量/StackMap 等调试帧
/// （原始字节补丁后旧调试表会失效，ProGuard 写行号时会越界）。
fn strip_code_debug_attrs(cf: &ClassFile, data: &[u8]) -> Vec<u8> {
    if data.len() < 8 {
        return data.to_vec();
    }
    let mut p = 0usize;
    let maxs = &data[0..4];
    p += 4;
    let clen = u32::from_be_bytes([data[p], data[p + 1], data[p + 2], data[p + 3]]) as usize;
    p += 4;
    if p + clen + 2 > data.len() {
        return data.to_vec();
    }
    let code = &data[p..p + clen];
    p += clen;
    let exn_len = u16::from_be_bytes([data[p], data[p + 1]]) as usize;
    p += 2;
    if p + exn_len * 8 + 2 > data.len() {
        return data.to_vec();
    }
    let exn = &data[p..p + exn_len * 8];
    p += exn_len * 8;
    let ac = u16::from_be_bytes([data[p], data[p + 1]]) as usize;
    p += 2;
    let mut kept: Vec<(u16, &[u8])> = Vec::new();
    for _ in 0..ac {
        if p + 6 > data.len() {
            break;
        }
        let ni = u16::from_be_bytes([data[p], data[p + 1]]);
        let alen = u32::from_be_bytes([data[p + 2], data[p + 3], data[p + 4], data[p + 5]])
            as usize;
        if p + 6 + alen > data.len() {
            break;
        }
        let body = &data[p + 6..p + 6 + alen];
        let nm = cf.utf8(ni).unwrap_or("");
        if !matches!(
            nm,
            "LineNumberTable"
                | "LocalVariableTable"
                | "LocalVariableTypeTable"
                | "StackMap"
                | "StackMapTable"
        ) {
            kept.push((ni, body));
        }
        p += 6 + alen;
    }
    let mut out = Vec::with_capacity(data.len());
    out.extend_from_slice(maxs);
    out.extend_from_slice(&(clen as u32).to_be_bytes());
    out.extend_from_slice(code);
    out.extend_from_slice(&(exn_len as u16).to_be_bytes());
    out.extend_from_slice(exn);
    out.extend_from_slice(&(kept.len() as u16).to_be_bytes());
    for (ni, body) in kept {
        out.extend_from_slice(&ni.to_be_bytes());
        out.extend_from_slice(&(body.len() as u32).to_be_bytes());
        out.extend_from_slice(body);
    }
    out
}

pub struct CodePatch {
    pub method: String,
    pub desc: String,
    pub off: usize,
    pub expect: Vec<u8>,
    pub new: Vec<u8>,
    pub label: String,
}

/// 在指定方法内应用"已人工验证"的原始字节补丁；字节不符直接报错。
pub fn apply_code_patches(
    payload: &[u8],
    class_name: &str,
    patches: &[CodePatch],
    log: &mut dyn FnMut(&str),
) -> Result<Vec<u8>, String> {
    let mut cf = ClassFile::parse(payload)?;
    if cf.this_name().as_deref() != Some(class_name) {
        return Ok(payload.to_vec());
    }
    let mut by_method: HashMap<(String, String), Vec<&CodePatch>> = HashMap::new();
    for p in patches {
        by_method
            .entry((p.method.clone(), p.desc.clone()))
            .or_default()
            .push(p);
    }
    let mut hit: HashSet<(String, String)> = HashSet::new();
    for mi in 0..cf.methods.len() {
        let key = (
            cf.utf8s(cf.methods[mi].name),
            cf.utf8s(cf.methods[mi].desc),
        );
        let rules = match by_method.get(&key) {
            Some(r) => r.clone(),
            None => continue,
        };
        for ai in 0..cf.methods[mi].attrs.len() {
            if cf.utf8(cf.methods[mi].attrs[ai].name) != Some("Code") {
                continue;
            }
            let mut blob = cf.methods[mi].attrs[ai].data.clone();
            for rule in &rules {
                let pos = 8 + rule.off;
                if pos + rule.expect.len() > blob.len() {
                    return Err(format!(
                        "字节码补丁[{}] {}.{} 偏移 {} 越界",
                        rule.label, class_name, key.0, rule.off
                    ));
                }
                let cur = &blob[pos..pos + rule.expect.len()];
                if cur != rule.expect.as_slice() {
                    return Err(format!(
                        "字节码补丁[{}] {}.{} 偏移 {} 期望 {} 实际 {}",
                        rule.label,
                        class_name,
                        key.0,
                        rule.off,
                        hex(rule.expect.as_slice()),
                        hex(cur)
                    ));
                }
                if pos + rule.new.len() > blob.len() {
                    return Err(format!(
                        "字节码补丁[{}] {}.{} 偏移 {} 新字节越界",
                        rule.label, class_name, key.0, rule.off
                    ));
                }
                blob[pos..pos + rule.new.len()].copy_from_slice(&rule.new);
                hit.insert(key.clone());
                log(&format!(
                    "字节码补丁: {} ({}.{})",
                    rule.label,
                    class_name.rsplit('/').next().unwrap_or(class_name),
                    key.0
                ));
            }
            cf.methods[mi].attrs[ai].data = blob;
        }
        // 补丁后调试信息失效：Code 去调试表，方法只保留安全属性
        for ai in 0..cf.methods[mi].attrs.len() {
            if cf.utf8(cf.methods[mi].attrs[ai].name) == Some("Code") {
                let d = cf.methods[mi].attrs[ai].data.clone();
                let nd = strip_code_debug_attrs(&cf, &d);
                cf.methods[mi].attrs[ai].data = nd;
            }
        }
        let keep: Vec<bool> = cf.methods[mi]
            .attrs
            .iter()
            .map(|a| {
                matches!(
                    cf.utf8(a.name),
                    Some("Code") | Some("Exceptions") | Some("Deprecated") | Some("Synthetic")
                )
            })
            .collect();
        let mut k = 0usize;
        cf.methods[mi].attrs.retain(|_| {
            let r = keep.get(k).copied().unwrap_or(false);
            k += 1;
            r
        });
    }
    if hit.len() != by_method.len() {
        return Err(format!("字节码补丁未命中方法: {class_name}"));
    }
    Ok(cf.build())
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}
