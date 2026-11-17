#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod archive;
mod assets;
mod batch;
mod bytecode;
mod classfile;
mod cod;
mod consts;
mod convert;
mod gui;
mod patch;
mod scan;
mod shim;
mod tools;

#[cfg(test)]
mod tests;

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_inner_size([1100.0, 780.0])
            .with_min_inner_size([920.0, 620.0]),
        ..Default::default()
    };
    eframe::run_native(
        "JarResizer 2.0",
        options,
        Box::new(|cc| Ok(Box::new(gui::App::new(cc)))),
    )
}
