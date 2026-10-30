package cn.kanokano.openwrt.mobile

import android.database.Cursor
import android.database.MatrixCursor
import android.os.CancellationSignal
import android.os.ParcelFileDescriptor
import android.provider.DocumentsContract
import android.provider.DocumentsProvider
import java.io.File
import java.io.FileNotFoundException

/** User-selected read-only access to backups and exports, never ADB private keys. */
class BackupDocuments : DocumentsProvider() {
    private val allowed = setOf("transactions", "release-indexes", "exports")
    private val base get() = File(context!!.filesDir, "openwrt").canonicalFile
    override fun onCreate() = true
    private fun file(id: String): File {
        if (id == "root") return base
        if (id.substringBefore('/') !in allowed) throw FileNotFoundException("Not an export or backup")
        val f = File(base, id).canonicalFile
        if (!f.toPath().startsWith(base.toPath()) || f == base) throw FileNotFoundException("Invalid path")
        val owner = File(base, id.substringBefore('/')).canonicalFile
        if (!f.toPath().startsWith(owner.toPath()) || owner.parentFile != base) throw FileNotFoundException("Invalid backup path")
        return f
    }
    private val columns = arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME,
        DocumentsContract.Document.COLUMN_MIME_TYPE, DocumentsContract.Document.COLUMN_FLAGS,
        DocumentsContract.Document.COLUMN_SIZE, DocumentsContract.Document.COLUMN_LAST_MODIFIED)
    private fun row(cursor: MatrixCursor, f: File) {
        val id = if (f == base) "root" else f.relativeTo(base).invariantSeparatorsPath
        val values = mapOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID to id,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME to if (f == base) "OpenWrt backups / 备份" else f.name,
            DocumentsContract.Document.COLUMN_MIME_TYPE to if (f.isDirectory) DocumentsContract.Document.MIME_TYPE_DIR else "application/octet-stream",
            DocumentsContract.Document.COLUMN_FLAGS to 0, DocumentsContract.Document.COLUMN_SIZE to if (f.isFile) f.length() else 0,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED to f.lastModified())
        cursor.addRow(cursor.columnNames.map { values[it] }.toTypedArray())
    }
    override fun queryRoots(projection: Array<out String>?): Cursor {
        val c = MatrixCursor(projection ?: arrayOf(DocumentsContract.Root.COLUMN_ROOT_ID, DocumentsContract.Root.COLUMN_DOCUMENT_ID,
            DocumentsContract.Root.COLUMN_TITLE, DocumentsContract.Root.COLUMN_FLAGS))
        val values = mapOf(DocumentsContract.Root.COLUMN_ROOT_ID to "openwrt", DocumentsContract.Root.COLUMN_DOCUMENT_ID to "root",
            DocumentsContract.Root.COLUMN_TITLE to "OpenWrt Tools", DocumentsContract.Root.COLUMN_FLAGS to DocumentsContract.Root.FLAG_LOCAL_ONLY)
        c.addRow(c.columnNames.map { values[it] }.toTypedArray());return c
    }
    override fun queryDocument(documentId: String, projection: Array<out String>?): Cursor = MatrixCursor(projection ?: columns).also { row(it, file(documentId)) }
    override fun queryChildDocuments(parentDocumentId: String, projection: Array<out String>?, sortOrder: String?): Cursor {
        val parent = file(parentDocumentId)
        return MatrixCursor(projection ?: columns).also { cursor ->
            parent.listFiles().orEmpty().filter { parent != base || it.name in allowed }.sortedBy { it.name }.forEach { row(cursor, it) }
        }
    }
    override fun openDocument(documentId: String, mode: String, signal: CancellationSignal?): ParcelFileDescriptor {
        if (mode != "r") throw FileNotFoundException("Backups are read-only")
        return ParcelFileDescriptor.open(file(documentId), ParcelFileDescriptor.MODE_READ_ONLY)
    }
}
