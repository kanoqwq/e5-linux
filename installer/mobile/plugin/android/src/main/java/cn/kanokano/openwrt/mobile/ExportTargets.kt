package cn.kanokano.openwrt.mobile

import java.io.File

/** Only a completed operation may publish its own selected document. */
class ExportTargets<T>(private val root: File) {
    private val pending = HashMap<String, T>()
    @Synchronized fun reserve(file: File, target: T) {
        require(file.canonicalFile.parentFile?.parentFile == root.canonicalFile) { "Invalid export workspace" }
        pending[file.absolutePath] = target
    }
    @Synchronized fun contains(path: String) = pending.containsKey(path)
    @Synchronized fun publish(path: String, writer: (File, T) -> Unit): Boolean {
        val target = pending[path] ?: return false
        val file = File(path)
        require(file.isFile) { "Export did not produce a file / 导出未生成文件" }
        writer(file, target)
        pending.remove(path)
        file.delete()
        file.parentFile?.delete()
        return true
    }
    @Synchronized fun discard(path: String) {
        if (pending.remove(path) != null) {
            val file = File(path)
            file.delete()
            file.parentFile?.delete()
        }
    }
}
