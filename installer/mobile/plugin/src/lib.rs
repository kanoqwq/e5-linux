use tauri::{plugin::{Builder, TauriPlugin}, Runtime};

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("mobile-host")
        .setup(|_app, api| {
            #[cfg(target_os = "android")]
            api.register_android_plugin("cn.kanokano.openwrt.mobile", "MobileHostPlugin")?;
            #[cfg(not(target_os = "android"))]
            let _ = api;
            Ok(())
        })
        .build()
}
