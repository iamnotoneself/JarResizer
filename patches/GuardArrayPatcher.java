import org.objectweb.asm.ClassAdapter;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.Label;
import org.objectweb.asm.MethodAdapter;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.util.Enumeration;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

/**
 * Generic guard for the shipped-game bug pattern:
 *
 *   GETSTATIC  someClass.stringArray : [Ljava/lang/String;
 *   ILOAD n
 *   AALOAD
 *
 * inside a method with descriptor (IZ)V, where the backing array has one
 * fewer element than the companion sprite array (g9 Ultraman: b.length ==
 * aD.length + 1, so the preload loop reaches aD[38] and crashes every time
 * a battle starts, on the original Nokia build as well).
 *
 * The AALOAD is replaced by a bounds-checked version that pushes null when
 * the index is out of range. The callee (q.a) catches the resulting
 * getResourceAsStream(null) internally, matching its missing-resource path.
 *
 * The transform is structural (no class/method names hardcoded), so it is a
 * no-op on jars that do not contain the exact pattern.
 */
public class GuardArrayPatcher {

    static int totalPatches;

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: GuardArrayPatcher in.jar out.jar");
            System.exit(2);
        }
        ZipFile zf = new ZipFile(new File(args[0]));
        ZipOutputStream zos = new ZipOutputStream(
                new java.io.FileOutputStream(args[1]));
        Enumeration e = zf.entries();
        while (e.hasMoreElements()) {
            ZipEntry ze = (ZipEntry) e.nextElement();
            byte[] data = readAll(zf.getInputStream(ze));
            if (ze.getName().endsWith(".class")) {
                try {
                    data = tryPatch(ze.getName(), data);
                } catch (Throwable t) {
                    System.out.println("GuardArrayPatcher skip " + ze.getName()
                            + ": " + t);
                }
            }
            ZipEntry ne = new ZipEntry(ze.getName());
            zos.putNextEntry(ne);
            zos.write(data);
            zos.closeEntry();
        }
        zos.close();
        zf.close();
        System.out.println("GuardArrayPatcher: " + totalPatches
                + " guarded array load(s)");
    }

    private static byte[] readAll(InputStream is) throws Exception {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = is.read(buf)) > 0) {
            bos.write(buf, 0, n);
        }
        is.close();
        return bos.toByteArray();
    }

    private static byte[] tryPatch(final String entry, byte[] in) {
        ClassReader cr = new ClassReader(in);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        final int[] hits = new int[1];
        cr.accept(new ClassAdapter(cw) {
            public MethodVisitor visitMethod(int access, String name,
                                             String desc, String sig,
                                             String[] exceptions) {
                MethodVisitor mv = super.visitMethod(access, name, desc, sig,
                        exceptions);
                if (desc.equals("(IZ)V")) {
                    return new GuardMV(mv, hits, entry);
                }
                return mv;
            }
        }, 0);
        if (hits[0] == 0) {
            return in;
        }
        totalPatches += hits[0];
        System.out.println("GuardArrayPatcher: " + entry + " x" + hits[0]);
        return cw.toByteArray();
    }

    static class GuardMV extends MethodAdapter {
        // 0 idle; 1 saw GETSTATIC [String; expecting ILOAD; 2 saw ILOAD
        int state = 0;
        final int[] hits;
        final String entry;

        GuardMV(MethodVisitor mv, int[] hits, String entry) {
            super(mv);
            this.hits = hits;
            this.entry = entry;
        }

        public void visitFieldInsn(int opcode, String owner, String name,
                                   String desc) {
            if (opcode == Opcodes.GETSTATIC && desc.startsWith("[")) {
                super.visitFieldInsn(opcode, owner, name, desc);
                state = 1;
                return;
            }
            state = 0;
            super.visitFieldInsn(opcode, owner, name, desc);
        }

        public void visitVarInsn(int opcode, int var) {
            if (state == 1 && opcode == Opcodes.ILOAD) {
                super.visitVarInsn(opcode, var);
                state = 2;
                return;
            }
            state = 0;
            super.visitVarInsn(opcode, var);
        }

        public void visitInsn(int opcode) {
            if (state == 2
                    && (opcode == Opcodes.AALOAD || opcode == Opcodes.BALOAD
                        || opcode == Opcodes.CALOAD || opcode == Opcodes.SALOAD
                        || opcode == Opcodes.IALOAD || opcode == Opcodes.LALOAD
                        || opcode == Opcodes.FALOAD || opcode == Opcodes.DALOAD)) {
                emitGuarded(opcode);
                state = 0;
                return;
            }
            state = 0;
            super.visitInsn(opcode);
        }

        // any other instruction breaks the sequence
        public void visitIntInsn(int o, int v) { state = 0; super.visitIntInsn(o, v); }
        public void visitLdcInsn(Object o) { state = 0; super.visitLdcInsn(o); }
        public void visitJumpInsn(int o, Label l) { state = 0; super.visitJumpInsn(o, l); }
        public void visitMethodInsn(int o, String a, String b, String c) { state = 0; super.visitMethodInsn(o, a, b, c); }
        public void visitTypeInsn(int o, String s) { state = 0; super.visitTypeInsn(o, s); }
        public void visitMultiANewArrayInsn(String s, int d) { state = 0; super.visitMultiANewArrayInsn(s, d); }

        private void emitGuarded(int loadOpcode) {
            // stack on entry: [array, index] ; value1=arraylength,
            // value2=index below: valid range is index < length, i.e.
            // length > index
            final int IA = 250;
            final int IR = 251;
            Label good = new Label();
            Label end = new Label();
            mv.visitVarInsn(Opcodes.ISTORE, IA); // [array]
            mv.visitVarInsn(Opcodes.ASTORE, IR); // []
            mv.visitVarInsn(Opcodes.ALOAD, IR);
            mv.visitInsn(Opcodes.ARRAYLENGTH);   // [len]
            mv.visitVarInsn(Opcodes.ILOAD, IA);  // [len, index]
            mv.visitJumpInsn(Opcodes.IF_ICMPGT, good); // len > index
            switch (loadOpcode) {
                case Opcodes.LALOAD: mv.visitInsn(Opcodes.LCONST_0); break;
                case Opcodes.DALOAD: mv.visitInsn(Opcodes.DCONST_0); break;
                case Opcodes.FALOAD: mv.visitInsn(Opcodes.FCONST_0); break;
                case Opcodes.AALOAD: mv.visitInsn(Opcodes.ACONST_NULL); break;
                default:             mv.visitInsn(Opcodes.ICONST_0); break;
            }
            mv.visitJumpInsn(Opcodes.GOTO, end);
            mv.visitLabel(good);
            mv.visitVarInsn(Opcodes.ALOAD, IR);
            mv.visitVarInsn(Opcodes.ILOAD, IA);
            mv.visitInsn(loadOpcode);
            mv.visitLabel(end);
            hits[0]++;
        }
    }
}
