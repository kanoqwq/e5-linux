fn main() {
    tauri_plugin::Builder::new(&["request", "native_command", "register_listener", "remove_listener"])
        .android_path("android")
        .build();
}
