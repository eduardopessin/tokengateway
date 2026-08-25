#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    std::panic::set_hook(Box::new(|info| {
        let msg = format!("Erro ao iniciar Quota Desktop:\n\n{}", info);
        #[cfg(target_os = "windows")]
        unsafe {
            use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
            use windows::core::HSTRING;
            let title = HSTRING::from("Quota Desktop — Erro");
            let body = HSTRING::from(msg);
            let _ = MessageBoxW(None, &body, &title, MB_OK | MB_ICONERROR);
        }
        #[cfg(not(target_os = "windows"))]
        eprintln!("{}", msg);
    }));

    quota_desktop_lib::run();
}
