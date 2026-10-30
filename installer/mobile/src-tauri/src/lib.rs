#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_mobile_host::init())
        .run(tauri::generate_context!())
        .expect("Cannot start OpenWrt Tools");
}
