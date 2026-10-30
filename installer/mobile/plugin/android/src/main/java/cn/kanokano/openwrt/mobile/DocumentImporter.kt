package cn.kanokano.openwrt.mobile

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/** Stream authorized documents once; never resolve content:// to guessed paths. */
class DocumentImporter(private val context: Context, private val progress: (String) -> Unit) {
    private var bytes = 0L
    private var files = 0
    private var last = 0L
    private val entries = sortedMapOf<String, String>()
    private val visited = hashSetOf<String>()
    private var directoryImport = false
    fun copy(uri: Uri, target: File, relative: String = target.name): String {
        val size = context.contentResolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)?.use {
            if (it.moveToFirst() && !it.isNull(0)) it.getLong(0) else -1L
        } ?: -1L
        require(++files <= 100000 && size <= (64L shl 30)) { "Document import exceeds limits / 导入文件超出限制" }
        require(size < 0 || target.parentFile!!.usableSpace >= size + (128L shl 20)) { "Not enough phone space / 手机空间不足" }
        val hash = MessageDigest.getInstance("SHA-256")
        var done = 0L
        try {
            context.contentResolver.openInputStream(uri)?.use { input -> target.outputStream().use { output ->
                val buffer = ByteArray(1024 * 1024)
                while (true) {
                    val count = input.read(buffer); if (count < 0) break
                    done += count;bytes += count
                    require(bytes <= (64L shl 30) && target.parentFile!!.usableSpace > (64L shl 20)) { "Not enough phone space / 手机空间不足" }
                    output.write(buffer, 0, count);hash.update(buffer, 0, count)
                    val now = System.currentTimeMillis()
                    if (now - last >= 200) { emit(if (directoryImport) bytes else done, if (directoryImport) 0 else size, relative);last = now }
                }
            } } ?: error("Cannot read selected document / 无法读取所选文件")
            require(size < 0 || done == size) { "Selected file changed while reading / 读取期间文件大小改变" }
            if (!directoryImport) emit(done, if (size < 0) done else size, relative)
            return hash.digest().joinToString("") { "%02x".format(it) }
        } catch (error: Exception) { target.delete();throw error }
    }
    private fun emit(done: Long, total: Long, name: String) = progress(JSONObject()
        .put("protocol", 1).put("type", "import_progress").put("data", JSONObject()
            .put("done", done).put("total", total).put("path", name)).toString())
    fun tree(uri: Uri, target: File): String {
        directoryImport = true
        fun walk(id: String, directory: File, depth: Int) {
            require(depth <= 48 && visited.add(id)) { "Unsafe document directory / 目录层级或循环不安全" }
            val children = DocumentsContract.buildChildDocumentsUriUsingTree(uri, id)
            val columns = arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME, DocumentsContract.Document.COLUMN_MIME_TYPE)
            context.contentResolver.query(children, columns, null, null, null)?.use { cursor ->
                while (cursor.moveToNext()) {
                    val childId = cursor.getString(0);val name = cursor.getString(1)
                    require(name.isNotEmpty() && name !in listOf(".", "..") && !name.any { it == '/' || it == '\\' || it.code < 32 }) { "Unsafe document name" }
                    val output = File(directory, name);require(!output.exists()) { "Duplicate document name" }
                    if (cursor.getString(2) == DocumentsContract.Document.MIME_TYPE_DIR) {
                        require(++files <= 100000);output.mkdir();walk(childId, output, depth + 1)
                    } else {
                        val relative = output.relativeTo(target).invariantSeparatorsPath
                        entries[relative] = copy(DocumentsContract.buildDocumentUriUsingTree(uri, childId), output, relative)
                    }
                }
            } ?: error("Cannot read selected directory / 无法读取所选目录")
        }
        target.mkdirs();walk(DocumentsContract.getTreeDocumentId(uri), target, 0)
        emit(bytes, bytes, target.name)
        val hash = MessageDigest.getInstance("SHA-256")
        entries.forEach { (name, digest) -> hash.update("$name\u0000$digest\n".toByteArray(Charsets.UTF_8)) }
        return hash.digest().joinToString("") { "%02x".format(it) }
    }
}
