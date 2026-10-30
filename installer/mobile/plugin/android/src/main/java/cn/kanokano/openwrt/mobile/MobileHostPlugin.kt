package cn.kanokano.openwrt.mobile

import android.app.Activity
import android.content.Intent
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.net.Uri
import android.provider.OpenableColumns
import android.provider.DocumentsContract
import android.webkit.WebView
import androidx.activity.result.ActivityResult
import androidx.core.content.ContextCompat
import androidx.core.app.ActivityCompat
import app.tauri.annotation.ActivityCallback
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.lang.ref.WeakReference
import java.util.UUID
import java.util.concurrent.Executors

@InvokeArg
class HostRequest { var command: String = ""; var parameters: String = "{}"; var language: String = "" }

/** Process-owned backend survives WebView recreation; no UI-thread device I/O. */
object MobileRuntime {
    private var runtime: PyObject? = null
    var listener = WeakReference<MobileHostPlugin>(null)
    lateinit var directory: File
    @Synchronized fun get(activity: Activity): PyObject {
        runtime?.let { return it }
        val context = activity.applicationContext
        directory = File(context.filesDir, "openwrt").apply { mkdirs() }
        val resources = File(context.filesDir, "openwrt-runtime").apply { mkdirs() }
        fun copyAssets(prefix: String, target: File) {
            context.assets.list(prefix).orEmpty().forEach { name ->
                val source = "$prefix/$name"; val dest = File(target, name)
                if (context.assets.list(source).orEmpty().isNotEmpty()) {
                    dest.mkdirs(); copyAssets(source, dest)
                } else context.assets.open(source).use { input -> dest.outputStream().use { input.copyTo(it) } }
            }
        }
        copyAssets("openwrt-runtime", resources)
        if (!Python.isStarted()) Python.start(AndroidPlatform(context))
        runtime = Python.getInstance().getModule("android_host.runtime")
            .callAttr("Runtime", this, UsbHost(context), directory.absolutePath, resources.absolutePath, context.applicationInfo.sourceDir)
        return runtime!!
    }
    fun emitProtocol(lines: String) {
        lines.lineSequence().filter { it.isNotBlank() }.forEach { line ->
            listener.get()?.emit(line)
        }
    }
}

@TauriPlugin
class MobileHostPlugin(private val activity: Activity) : Plugin(activity) {
    private val worker = Executors.newSingleThreadExecutor()
    private val exports by lazy { ExportTargets<Uri>(File(activity.filesDir, "openwrt/exports")) }
    private var dialog: Triple<String, String, String>? = null
    override fun load(webView: WebView) {
        super.load(webView)
        MobileRuntime.listener = WeakReference(this)
    }
    fun emit(line: String) {
        val event = JSObject(line)
        trigger("backend", event)
        val type = event.optString("type")
        val data = event.optJSONObject("data")
        if (type == "transaction") {
            val status = data?.optString("status")
            if (status in listOf("completed", "failed", "cancelled")) stopTask()
        }
    }
    private fun startTask(local: Boolean = false) {
        val preferences = activity.getSharedPreferences("openwrt-tools", Context.MODE_PRIVATE)
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(activity, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED && !preferences.getBoolean("notification-requested", false)) {
            preferences.edit().putBoolean("notification-requested", true).apply()
            activity.runOnUiThread { ActivityCompat.requestPermissions(activity, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 410) }
        }
        InstallerTaskService.beginTask(activity.applicationContext, local)
    }
    private fun language(args: HostRequest) {
        if (args.language in listOf("zh", "en")) activity.getSharedPreferences("openwrt-tools", Context.MODE_PRIVATE).edit().putString("language", args.language).apply()
    }
    private fun stopTask() { InstallerTaskService.finishTask(activity.applicationContext) }
    private fun resolve(invoke: Invoke, value: Any?, key: String = "value") {
        invoke.resolve(JSObject().put(key, value ?: JSONObject.NULL))
    }
    private fun publishExport(path: String) {
        exports.publish(path) { file, uri ->
            try {
                activity.contentResolver.openOutputStream(uri, "wt")?.use { output -> file.inputStream().use { it.copyTo(output) } }
                    ?: error("Cannot open export destination / 无法写入导出文件")
            } catch (error: Exception) {
                throw IllegalStateException("Cannot save export; completed file kept in About & backups / 无法保存导出文件；完整文件已保留在关于与备份：${file.name}", error)
            }
        }
    }
    @Command fun request(invoke: Invoke) {
        val args = invoke.parseArgs(HostRequest::class.java)
        language(args)
        worker.execute {
            var completed = false
            try {
                if (args.command in listOf("start", "resume", "retry", "probe", "probe_update", "plan", "export", "import", "delete_record", "ota_analyze", "ota_export", "ota_apply", "mobile_index")) startTask(args.command in listOf("delete_record", "export", "import", "ota_analyze", "ota_export", "mobile_index"))
                val runtime = MobileRuntime.get(activity)
                val result = org.json.JSONTokener(runtime.callAttr("request", args.command, args.parameters).toString()).nextValue()
                completed = true
                if (result is JSONObject) listOf("path", "output").forEach { publishExport(result.optString(it)) }
                resolve(invoke, result, "result")
                if (!runtime.callAttr("busy").toBoolean()) stopTask()
            } catch (error: Exception) {
                if (!completed) {
                    val parameters = runCatching { JSONObject(args.parameters) }.getOrDefault(JSONObject())
                    if (args.command == "export") exports.discard(parameters.optString("path"))
                    if (args.command in listOf("ota_export", "mobile_index")) exports.discard(parameters.optString("output"))
                }
                val running = try { MobileRuntime.get(activity).callAttr("busy").toBoolean() } catch (_: Exception) { false }
                if (running) startTask() else stopTask()
                invoke.reject(error.message ?: "Operation failed / 操作失败")
            }
        }
    }
    @Command fun nativeCommand(invoke: Invoke) {
        val args = invoke.parseArgs(HostRequest::class.java)
        language(args)
        val options = JSONObject(args.parameters)
        when (args.command) {
            "copy_text" -> {
                val text = options.getString("text")
                if (text.length > 2 * 1024 * 1024) { invoke.reject("Clipboard text exceeds limit / 复制内容过大");return }
                (activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("OpenWrt", text))
                resolve(invoke, true)
            }
            "pick_package", "pick_package_directory", "pick_index_directory", "pick_ota_input", "pick_recovery",
            "save_backup", "save_log", "save_index", "save_ota" -> activity.runOnUiThread {
                try { pick(invoke, args.command, args.command.startsWith("save_"), options) }
                catch (error: Exception) {
                    dialog = null
                    invoke.reject("Cannot open Android file picker / 无法打开 Android 文件选择器：${error.message}", error)
                }
            }
            "write_log" -> worker.execute {
                try {
                    val path = options.getString("path")
                    require(exports.contains(path)) { "Select an export destination first" }
                    try { File(path).writeText(options.getString("text")) } catch (error: Exception) { exports.discard(path);throw error }
                    publishExport(path);resolve(invoke, true)
                } catch (error: Exception) { invoke.reject(error.message) }
            }
            "open_repository" -> {
                val url = when (options.optString("repository")) {
                    "e5" -> "https://github.com/sprd-linux-devs/e5-linux"
                    "mu300" -> "https://github.com/dikeckaan/mu300-linux"
                    else -> { invoke.reject("Unknown project repository"); return }
                }
                activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)));resolve(invoke, true)
            }
            "open_data", "open_index_data" -> {
                // Android's document UI uses a registered document provider.
                val root = Uri.parse("content://${activity.packageName}.documents/root/openwrt")
                activity.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(root, "vnd.android.document/root").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
                resolve(invoke, true)
            }
            else -> invoke.reject("Unsupported native command: ${args.command}")
        }
    }
    private fun pick(invoke: Invoke, command: String, save: Boolean, options: JSONObject) {
        if (dialog != null) { invoke.reject("Another file picker is open / 文件选择器已打开"); return }
        val extension = when (command) { "save_index" -> ".owrt-index.json"; "save_log" -> ".log"; else -> ".zip" }
        val raw = options.optString("suggested_name").ifBlank { options.optString("suggestedName") }.ifBlank { "OpenWrt-" + if (command == "save_log") "Installer.log" else if (command == "save_index") "Version.owrt-index.json" else "Backup$extension" }
        val suggested = safeName(raw)
        dialog = Triple(command, suggested, options.optString("kind"))
        val intent = if (command in listOf("pick_package_directory", "pick_index_directory")) Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
            else Intent(if (save) Intent.ACTION_CREATE_DOCUMENT else Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
        if (save) intent.putExtra(Intent.EXTRA_TITLE, suggested)
        startActivityForResult(invoke, intent, "picked")
    }
    @ActivityCallback fun picked(invoke: Invoke, result: ActivityResult) {
        val state = dialog;dialog = null
        val uri = result.data?.data
        if (state == null || result.resultCode != Activity.RESULT_OK || uri == null) { resolve(invoke, null);return }
        worker.execute {
            try {
                startTask(true)
                val runtime = MobileRuntime.get(activity)
                val (command, suggestion, kind) = state
                if (command in listOf("pick_package_directory", "pick_index_directory")) {
                    val document = DocumentsContract.buildDocumentUriUsingTree(uri, DocumentsContract.getTreeDocumentId(uri))
                    val name = activity.contentResolver.query(document, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                        if (it.moveToFirst()) safeName(it.getString(0)) else "directory"
                    } ?: "directory"
                    val target = File(MobileRuntime.directory, "imports/${UUID.randomUUID()}/$name")
                    val digest = DocumentImporter(activity.applicationContext, ::emit).tree(uri, target)
                    resolve(invoke, runtime.callAttr("import_directory", target.absolutePath, digest, if (command == "pick_index_directory") "index" else "package").toString())
                    return@execute
                }
                val suggested = activity.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                    if (it.moveToFirst()) safeName(it.getString(0)) else suggestion
                } ?: suggestion
                if (command.startsWith("save_")) {
                    val path = File(MobileRuntime.directory, "exports/${UUID.randomUUID()}/$suggested")
                    path.parentFile!!.mkdirs()
                    exports.reserve(path, uri)
                    resolve(invoke, path.absolutePath)
                } else {
                    val cache = File(MobileRuntime.directory, "imports/${UUID.randomUUID()}/$suggested")
                    cache.parentFile!!.mkdirs()
                    DocumentImporter(activity.applicationContext, ::emit).copy(uri, cache)
                    val selected = when (command) {
                        "pick_package" -> runtime.callAttr("import_package", cache.absolutePath).toString()
                        "pick_recovery" -> runtime.callAttr("import_recovery", cache.absolutePath).toString()
                        else -> if (kind == "patch") runtime.callAttr("import_patch", cache.absolutePath).toString()
                                else runtime.callAttr("retain_input", cache.absolutePath).toString()
                    }
                    if (command in listOf("pick_package", "pick_recovery")) { cache.delete();cache.parentFile?.delete() }
                    resolve(invoke, selected)
                }
            } catch (error: Exception) { invoke.reject(error.message ?: "File operation failed / 文件操作失败") }
            finally { val running = try { MobileRuntime.get(activity).callAttr("busy").toBoolean() } catch (_: Exception) { false };if (running) startTask() else stopTask() }
        }
    }
    private fun safeName(value: String): String {
        var name = value.replace(Regex("[\\x00-\\x1f\\\\/:*?\"<>|]"), "_").trim()
        while (name.toByteArray(Charsets.UTF_8).size > 240) name = name.dropLast(Character.charCount(name.codePointBefore(name.length)))
        return name.takeUnless { it.isEmpty() || it == "." || it == ".." } ?: "OpenWrt-file"
    }
}
