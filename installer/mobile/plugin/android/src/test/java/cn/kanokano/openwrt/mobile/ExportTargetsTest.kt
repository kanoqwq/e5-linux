package cn.kanokano.openwrt.mobile

import java.io.File
import java.nio.file.Files
import org.junit.Assert.*
import org.junit.Test

class ExportTargetsTest {
    @Test fun onlyCompletedOperationPublishesItsSelectedFile() {
        val root = Files.createTempDirectory("openwrt-export-test").toFile()
        try {
            val targets = ExportTargets<String>(root)
            val failed = File(root, "first/partial.zip").apply { parentFile.mkdirs(); writeText("partial") }
            val complete = File(root, "second/result.zip").apply { parentFile.mkdirs(); writeText("complete") }
            targets.reserve(failed, "first-document")
            targets.reserve(complete, "second-document")
            val saved = mutableListOf<String>()
            assertFalse(targets.publish("unrelated") { _, _ -> fail("No unrelated export") })
            assertTrue(targets.publish(complete.path) { file, _ -> saved.add(file.readText()) })
            assertEquals(listOf("complete"), saved)
            assertTrue(failed.isFile)
            targets.discard(failed.path)
            assertFalse(failed.exists())
            assertFalse(complete.exists())
        } finally { root.deleteRecursively() }
    }
    @Test fun documentWriteFailureKeepsCompletedFileForUserRecovery() {
        val root = Files.createTempDirectory("openwrt-export-test").toFile()
        try {
            val targets = ExportTargets<String>(root)
            val complete = File(root, "job/result.zip").apply { parentFile.mkdirs(); writeText("complete") }
            targets.reserve(complete, "document")
            try { targets.publish(complete.path) { _, _ -> error("Document provider unavailable") }; fail("Expected failure") }
            catch (_: IllegalStateException) { }
            assertEquals("complete", complete.readText())
            assertTrue(targets.contains(complete.path))
            assertTrue(targets.publish(complete.path) { _, _ -> })
            assertFalse(complete.exists())
        } finally { root.deleteRecursively() }
    }
    @Test fun cannotReserveOutsideOwnedExportWorkspace() {
        val root = Files.createTempDirectory("openwrt-export-test").toFile()
        try {
            try { ExportTargets<String>(root).reserve(File(root.parentFile, "unowned.zip"), "document"); fail("Expected rejection") }
            catch (_: IllegalArgumentException) { }
        } finally { root.deleteRecursively() }
    }
}
