mod stack;
mod updater;

use stack::{set_home, stack_status, start_stack, stop_stack, update_stack, write_hosts};
use updater::{check_update, install_update};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            stack_status,
            start_stack,
            stop_stack,
            update_stack,
            set_home,
            write_hosts,
            check_update,
            install_update,
        ])
        .run(tauri::generate_context!())
        .expect("error while running SuperBoss desktop");
}
