import org.objectweb.asm.*;
import java.io.*;
import java.util.*;
import java.util.zip.*;

/**
 * Move selected methods out of one "fat" class into a new sibling class
 * so rapc can place them into separate COD code sections (its per-section
 * cap is ~64 KB; one Gameloft class may carry 68+ KB of bytecode).
 *
 *   java ClassSplit in.jar out.jar owner new "l(I)V" ["e(I)V" ...]
 *
 * Instance methods become `public static` with the owner type prepended
 * as first parameter; the local variable slots stay identical (slot 0
 * is `this`, still typed as owner), so all bytecode, exception tables
 * and StackMap frames remain valid. All call sites across the jar are
 * redirected (invokevirtual -> invokestatic). Static methods keep their
 * descriptor. Every private field/method of the owner is relaxed to
 * package scope (same package as the sibling), and former
 * invokespecial-private calls become invokevirtual.
 *
 * ASM 3.x API (interfaces, 4-arg visitMethodInsn).
 */
public class ClassSplit {

    static String OWNER, NEW;
    static HashSet specs = new HashSet();

    static String key(String name, String desc) { return name + desc; }

    static String staticDesc(String name, String desc, boolean isStatic) {
        if (isStatic) return desc;
        return "(L" + OWNER + ";" + desc.substring(1);
    }

    static class MV implements MethodVisitor {
        final MethodVisitor d;
        MV(MethodVisitor d) { this.d = d; }
        public AnnotationVisitor visitAnnotationDefault() {
            return d.visitAnnotationDefault(); }
        public AnnotationVisitor visitAnnotation(String de, boolean v) {
            return d.visitAnnotation(de, v); }
        public AnnotationVisitor visitParameterAnnotation(int p, String de,
                boolean v) { return d.visitParameterAnnotation(p, de, v); }
        public void visitAttribute(Attribute a) { d.visitAttribute(a); }
        public void visitCode() { d.visitCode(); }
        public void visitFrame(int t, int n, Object[] lo, int nn,
                Object[] st) { d.visitFrame(t, n, lo, nn, st); }
        public void visitInsn(int op) { d.visitInsn(op); }
        public void visitIntInsn(int op, int v) { d.visitIntInsn(op, v); }
        public void visitVarInsn(int op, int v) { d.visitVarInsn(op, v); }
        public void visitTypeInsn(int op, String t) {
            d.visitTypeInsn(op, t); }
        public void visitFieldInsn(int op, String o, String n, String de) {
            d.visitFieldInsn(op, o, n, de); }
        public void visitMethodInsn(int op, String o, String n, String de) {
            if (o.equals(OWNER) && specs.contains(key(n, de))) {
                boolean isStaticCall = (op == Opcodes.INVOKESTATIC);
                d.visitMethodInsn(Opcodes.INVOKESTATIC, NEW, n,
                        isStaticCall ? de : "(L" + OWNER + ";"
                                + de.substring(1));
                return;
            }
            if (op == Opcodes.INVOKESPECIAL && o.equals(OWNER)
                    && !n.equals("<init>")) {
                // private same-class methods were relaxed to package scope
                d.visitMethodInsn(Opcodes.INVOKEVIRTUAL, o, n, de);
                return;
            }
            d.visitMethodInsn(op, o, n, de);
        }
        public void visitJumpInsn(int op, Label l) { d.visitJumpInsn(op, l); }
        public void visitLabel(Label l) { d.visitLabel(l); }
        public void visitLdcInsn(Object c) { d.visitLdcInsn(c); }
        public void visitIincInsn(int v, int i) { d.visitIincInsn(v, i); }
        public void visitTableSwitchInsn(int mi, int ma, Label d2,
                Label[] h) { d.visitTableSwitchInsn(mi, ma, d2, h); }
        public void visitLookupSwitchInsn(String t) { }
        public void visitLookupSwitchInsn(Label d2, int[] k, Label[] h) {
            d.visitLookupSwitchInsn(d2, k, h); }
        public void visitMultiANewArrayInsn(String de, int ds) {
            d.visitMultiANewArrayInsn(de, ds); }
        public void visitTryCatchBlock(Label st, Label en, Label h, String t) {
            d.visitTryCatchBlock(st, en, h, t); }
        public void visitLocalVariable(String n, String de, String s,
                Label st, Label en, int i) {
            d.visitLocalVariable(n, de, s, st, en, i); }
        public void visitLineNumber(int l, Label st) {
            d.visitLineNumber(l, st); }
        public void visitMaxs(int ms, int ml) { d.visitMaxs(ms, ml); }
        public void visitEnd() { d.visitEnd(); }
    }

    public static void main(String[] a) throws Exception {
        String in = a[0], out = a[1];
        OWNER = a[2]; NEW = a[3];
        for (int i = 4; i < a.length; i++) specs.add(a[i]);

        ZipFile zin = new ZipFile(in);
        ZipEntry oe = zin.getEntry(OWNER + ".class");
        if (oe == null) throw new IOException("owner class not found: "
                + OWNER);
        byte[] ob;
        {
            InputStream is = zin.getInputStream(oe);
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] buf = new byte[8192]; int n;
            while ((n = is.read(buf)) > 0) bo.write(buf, 0, n);
            ob = bo.toByteArray();
        }
        ClassReader cr = new ClassReader(ob);
        // NOTE: plain ClassWriter(0), not ClassWriter(cr,0): the latter
        // copies unused constant-pool entries from the reader, leaving a
        // dangling Methodref to the moved method which ProGuard rejects.
        final ClassWriter cw = new ClassWriter(0);
        final ClassWriter nw = new ClassWriter(0);

        ClassVisitor cv = new ClassVisitor() {
            public void visit(int ver, int acc, String name, String sig,
                    String sup, String[] in) {
                nw.visit(ver, Opcodes.ACC_PUBLIC, NEW, null,
                        "java/lang/Object", null);
                cw.visit(ver, acc, name, sig, sup, in);
            }
            public void visitSource(String n, String de) {
                cw.visitSource(n, de); }
            public void visitOuterClass(String o, String n, String de) {
                cw.visitOuterClass(o, n, de); }
            public AnnotationVisitor visitAnnotation(String de, boolean v) {
                return cw.visitAnnotation(de, v); }
            public void visitAttribute(Attribute at) { cw.visitAttribute(at); }
            public void visitInnerClass(String n, String ou, String in,
                    int acc) { cw.visitInnerClass(n, ou, in, acc); }
            public FieldVisitor visitField(int acc, String n, String de,
                    String sig, Object v) {
                return cw.visitField(acc & ~Opcodes.ACC_PRIVATE, n, de, sig,
                        v);
            }
            public MethodVisitor visitMethod(int acc, String name, String de,
                    String sig, String[] ex) {
                boolean match = specs.contains(key(name, de));
                if (match) {
                    boolean isStatic = (acc & Opcodes.ACC_STATIC) != 0;
                    String nde = staticDesc(name, de, isStatic);
                    int nacc = Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC
                            | (acc & (Opcodes.ACC_FINAL
                                      | Opcodes.ACC_SYNCHRONIZED
                                      | Opcodes.ACC_STRICT));
                    MethodVisitor t = nw.visitMethod(nacc, name, nde, sig, ex);
                    return new MV(t);
                }
                MethodVisitor t = cw.visitMethod(
                        acc & ~Opcodes.ACC_PRIVATE, name, de, sig, ex);
                return t == null ? null : new MV(t);
            }
            public void visitEnd() {
                MethodVisitor ci = nw.visitMethod(Opcodes.ACC_PUBLIC,
                        "<init>", "()V", null, null);
                ci.visitCode();
                ci.visitVarInsn(Opcodes.ALOAD, 0);
                ci.visitMethodInsn(Opcodes.INVOKESPECIAL,
                        "java/lang/Object", "<init>", "()V");
                ci.visitInsn(Opcodes.RETURN);
                ci.visitMaxs(1, 1);
                ci.visitEnd();
                nw.visitEnd();
                cw.visitEnd();
            }
        };
        cr.accept(cv, 0);
        byte[] newOwner = cw.toByteArray();
        byte[] newClass = nw.toByteArray();

        ZipOutputStream zo = new ZipOutputStream(new FileOutputStream(out));
        Enumeration e = zin.entries();
        byte[] buf = new byte[8192];
        while (e.hasMoreElements()) {
            ZipEntry en = (ZipEntry) e.nextElement();
            if (en.isDirectory()) continue;
            byte[] data = null;
            if (en.getName().equals(OWNER + ".class")) data = newOwner;
            else if (en.getName().equals(NEW + ".class")) data = newClass;
            zo.putNextEntry(new ZipEntry(en.getName()));
            if (data != null) {
                zo.write(data);
            } else {
                InputStream is = zin.getInputStream(en);
                int n;
                while ((n = is.read(buf)) > 0) zo.write(buf, 0, n);
            }
            zo.closeEntry();
        }
        if (zin.getEntry(NEW + ".class") == null) {
            zo.putNextEntry(new ZipEntry(NEW + ".class"));
            zo.write(newClass);
            zo.closeEntry();
        }
        zo.close();
        zin.close();
        System.out.println("split OK: " + OWNER + " -> " + NEW + " ("
                + (a.length - 4) + " methods)");
    }
}
