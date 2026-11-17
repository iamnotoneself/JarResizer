//! 线性字节码遍历。与 Python 版 `walk_code` 完全一致：不跟随分支，
//! 只按指令长度前进（tableswitch/lookupswitch/wide 特殊处理）。

#[derive(Clone, Debug)]
pub struct Instr {
    pub off: usize,
    pub op: u8,
    pub operand: Vec<u8>,
    /// 指令总长度（含操作码），供测试/诊断使用。
    #[allow(dead_code)]
    pub len: usize,
}

pub fn op_extra_table() -> [usize; 256] {
    let mut t = [0usize; 256];
    for op in 0x00..=0x09 {
        t[op] = 0;
    }
    t[0x10] = 1; // bipush
    t[0x11] = 2; // sipush
    t[0x12] = 1; // ldc
    t[0x13] = 2; // ldc_w
    t[0x14] = 2; // ldc2_w
    for op in 0x15..=0x19 {
        t[op] = 1;
    } // iload..aload
    for op in 0x36..=0x3a {
        t[op] = 1;
    } // istore..astore
    t[0x84] = 2; // iinc
    for op in 0x99..=0xa8 {
        t[op] = 2;
    } // ifeq..if_acmpne, goto, jsr
    t[0xa9] = 1; // ret
    for op in [0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xbb, 0xbd, 0xc0, 0xc1] {
        t[op] = 2;
    }
    t[0xb9] = 4; // invokeinterface
    t[0xba] = 4; // invokedynamic
    t[0xbc] = 1; // newarray
    t[0xc5] = 3; // multianewarray
    t[0xc6] = 2; // ifnull
    t[0xc7] = 2; // ifnonnull
    t[0xc8] = 4; // goto_w
    t[0xc9] = 4; // jsr_w
    t
}

fn be_i32(d: &[u8], p: usize) -> Option<i32> {
    if p + 4 > d.len() {
        return None;
    }
    Some(i32::from_be_bytes([d[p], d[p + 1], d[p + 2], d[p + 3]]))
}

pub fn walk_code(code: &[u8]) -> Vec<Instr> {
    let t = op_extra_table();
    let mut out = Vec::new();
    let n = code.len();
    let mut i = 0usize;
    while i < n {
        let op = code[i];
        if op == 0xaa {
            // tableswitch
            let pad = (4 - ((i + 1) % 4)) % 4;
            let base = i + 1 + pad;
            let low = match base.checked_add(4).and_then(|p| be_i32(code, p)) {
                Some(v) => v,
                None => {
                    out.push(Instr {
                        off: i,
                        op,
                        operand: code[i + 1..].to_vec(),
                        len: n - i,
                    });
                    break;
                }
            };
            let high = match base.checked_add(8).and_then(|p| be_i32(code, p)) {
                Some(v) => v,
                None => {
                    out.push(Instr {
                        off: i,
                        op,
                        operand: code[i + 1..].to_vec(),
                        len: n - i,
                    });
                    break;
                }
            };
            let cnt = (high as i64 - low as i64 + 1).max(0) as usize;
            let end = base + 12 + cnt * 4;
            let end = end.min(n);
            out.push(Instr {
                off: i,
                op,
                operand: code[i + 1..end].to_vec(),
                len: end - i,
            });
            i = end;
            continue;
        }
        if op == 0xab {
            // lookupswitch
            let pad = (4 - ((i + 1) % 4)) % 4;
            let base = i + 1 + pad;
            let npairs = match base.checked_add(4).and_then(|p| be_i32(code, p)) {
                Some(v) if v >= 0 => v as usize,
                _ => {
                    out.push(Instr {
                        off: i,
                        op,
                        operand: code[i + 1..].to_vec(),
                        len: n - i,
                    });
                    break;
                }
            };
            let end = (base + 8 + npairs * 8).min(n);
            out.push(Instr {
                off: i,
                op,
                operand: code[i + 1..end].to_vec(),
                len: end - i,
            });
            i = end;
            continue;
        }
        if op == 0xc4 {
            // wide
            let sub = *code.get(i + 1).unwrap_or(&0);
            let size = if sub == 0x84 { 4 } else { 3 };
            let end = (i + 1 + size).min(n);
            out.push(Instr {
                off: i,
                op,
                operand: code[i + 1..end].to_vec(),
                len: end - i,
            });
            i = end;
            continue;
        }
        let extra = t[op as usize];
        let end = (i + 1 + extra).min(n);
        out.push(Instr {
            off: i,
            op,
            operand: code[i + 1..end].to_vec(),
            len: end - i,
        });
        i = end;
    }
    out
}

/// BIPUSH/SIPUSH 的有符号值。
pub fn push_value(op: u8, operand: &[u8]) -> Option<i32> {
    match op {
        0x10 if !operand.is_empty() => Some(operand[0] as i8 as i32),
        0x11 if operand.len() >= 2 => Some(i16::from_be_bytes([operand[0], operand[1]]) as i32),
        _ => None,
    }
}
