plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python") version "17.2.0"
}

android {
    namespace = "cn.kanokano.openwrt.mobile"
    compileSdk = 36
    defaultConfig {
        minSdk = 26
        ndk { abiFilters += (providers.gradleProperty("abiList").orNull ?: "arm64-v8a").split(',') }
        consumerProguardFiles("consumer-rules.pro")
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    kotlinOptions { jvmTarget = "1.8" }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        buildPython(requireNotNull(System.getenv("OPENWRT_BUILD_PYTHON")) { "Select matching build Python through build-android.py" })
        pip {
            install(file("../../vendor/adb_shell-0.4.4-py3-none-any.whl").absolutePath)
            install("paramiko==3.5.1")
            // CPython 3.13 wheels verified with 16 KiB ELF LOAD alignment.
            install("cryptography==42.0.8")
            install("cffi==1.17.1")
            install("bcrypt==3.2.2")
            install("PyNaCl==1.5.0")
            install("lz4==4.3.3")
            install("dissect.extfs==3.15")
        }
    }
    sourceSets.getByName("main") {
        srcDir("../../python")
        srcDir(layout.buildDirectory.dir("generated/python").get().asFile)
    }
}

val stagePython by tasks.registering(Sync::class) {
    from("../../../backend") { into("backend"); exclude("__pycache__/**") }
    into(layout.buildDirectory.dir("generated/python"))
}
val stageResources by tasks.registering(Sync::class) {
    into(layout.buildDirectory.dir("generated/assets/openwrt-runtime"))
    from("../../../runtime/storage-e5.sh")
    from("../../../runtime/storage-mu300.sh")
    from("../../../device/f2fs-online-arm64")
    from("../../../device/build.json") { into("online-helper") }
    from("../../../runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota") {
        include("engine.py", "boot.py", "rpc.py", "proxy.sh")
    }
    from("../../../runtime/luci-app-unisoc-ota/htdocs/luci-static/resources/view/system/unisoc-ota.js")
    from("../../../runtime/luci-app-unisoc-ota/htdocs/luci-static/resources/unisoc-ota/errors.js")
    from("../../../runtime/luci-app-unisoc-ota/root/usr/share/rpcd/acl.d/unisoc-ota.json")
    from("../../../ota-native/lz4raw-aarch64.so")
    from("../../../release-index.py")
}
// AGP 9.2+ issue 550220873: use the new-typed block overload for libraries.
android.sourceSets {
    named("main") { assets.srcDir(layout.buildDirectory.dir("generated/assets").get().asFile) }
}
tasks.named("preBuild") { dependsOn(stagePython, stageResources) }
tasks.matching { it.name.contains("Python") && it.name != stagePython.name }.configureEach { dependsOn(stagePython) }

dependencies {
    testImplementation("junit:junit:4.13.2")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation(project(":tauri-android"))
}
