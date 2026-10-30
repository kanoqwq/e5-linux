package cn.kanokano.openwrt.mobile

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.hardware.usb.*
import android.os.Build
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** Opens only the authorized ADB interface, never MTP or a block device. */
class UsbHost(private val context: Context) {
    private val manager = context.getSystemService(Context.USB_SERVICE) as UsbManager
    private data class Link(val connection: UsbDeviceConnection, val iface: UsbInterface, val input: UsbEndpoint, val output: UsbEndpoint)
    private val links = ConcurrentHashMap<String, Link>()
    private val requested = ConcurrentHashMap.newKeySet<String>()
    private fun iface(device: UsbDevice): UsbInterface? = (0 until device.interfaceCount).map { device.getInterface(it) }
        .firstOrNull { it.interfaceClass == 255 && it.interfaceSubclass == 66 && it.interfaceProtocol == 1 }
    private fun serial(device: UsbDevice): String {
        val identity = if (manager.hasPermission(device)) device.serialNumber else null
        return identity ?: "usb:${device.vendorId}:${device.productId}:${device.deviceName}"
    }
    private fun device(serial: String) = manager.deviceList.values.firstOrNull { iface(it) != null && serial(it) == serial }
        ?: throw IOException("USB device disconnected / USB 设备已断开")
    fun enumerate(): String = JSONArray().apply {
        requested.retainAll(manager.deviceList.keys)
        manager.deviceList.values.filter { iface(it) != null }.forEach {
            put(JSONObject().put("serial", serial(it)).put("permission", manager.hasPermission(it))
                .put("label", if (manager.hasPermission(it)) it.productName ?: "USB Android" else "USB Android"))
        }
    }.toString()
    fun requestPermission(serial: String) {
        val d = device(serial)
        if (!manager.hasPermission(d) && requested.add(d.deviceName)) {
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
            val pending = PendingIntent.getBroadcast(context, d.deviceId,
                Intent(context.packageName + ".USB_PERMISSION").setPackage(context.packageName), flags)
            manager.requestPermission(d, pending)
        }
    }
    fun retryPermissions() { requested.clear() }
    fun open(serial: String): String {
        val d = device(serial)
        if (!manager.hasPermission(d)) throw SecurityException("USB permission is required / 需要 USB 授权")
        val i = iface(d) ?: throw IOException("No ADB interface")
        val endpoints = (0 until i.endpointCount).map { i.getEndpoint(it) }.filter { it.type == UsbConstants.USB_ENDPOINT_XFER_BULK }
        val input = endpoints.firstOrNull { it.direction == UsbConstants.USB_DIR_IN } ?: throw IOException("No ADB input")
        val output = endpoints.firstOrNull { it.direction == UsbConstants.USB_DIR_OUT } ?: throw IOException("No ADB output")
        val connection = manager.openDevice(d) ?: throw IOException("Cannot open USB device")
        if (!connection.claimInterface(i, true)) { connection.close(); throw IOException("ADB interface is busy") }
        return UUID.randomUUID().toString().also { links[it] = Link(connection, i, input, output) }
    }
    fun close(handle: String) { links.remove(handle)?.let { it.connection.releaseInterface(it.iface); it.connection.close() } }
    fun read(handle: String, count: Int, timeout: Int): ByteArray {
        val link = links[handle] ?: throw IOException("USB connection closed")
        require(count in 1..(4 shl 20))
        val bytes = ByteArray(minOf(count, if (Build.VERSION.SDK_INT >= 28) 65536 else 16384))
        val deadline = SystemClock.elapsedRealtime() + timeout.coerceAtLeast(1)
        while (true) {
            val remaining = (deadline - SystemClock.elapsedRealtime()).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
            if (remaining <= 0) throw IOException("USB read timed out / USB 读取超时")
            val n = link.connection.bulkTransfer(link.input, bytes, bytes.size, remaining)
            if (n < 0) throw IOException("USB read failed or device disconnected / USB 读取失败或设备已断开")
            if (n > 0) return bytes.copyOf(n)
            // A zero-length packet terminates a USB transfer, not the ADB
            // connection. Wait for the next bytes within the same deadline.
        }
    }
    fun write(handle: String, bytes: ByteArray, timeout: Int): Int {
        val link = links[handle] ?: throw IOException("USB connection closed")
        var sent = 0
        while (sent < bytes.size) {
            val count = minOf(if (Build.VERSION.SDK_INT >= 28) 65536 else 16384, bytes.size - sent)
            val n = link.connection.bulkTransfer(link.output, bytes, sent, count, timeout)
            if (n <= 0) throw IOException("USB write failed / USB 写入失败")
            sent += n
        }
        return sent
    }
}
