#[allow(dead_code)]
#[path = "src/resource_layout.rs"]
mod resource_layout;

use resource_layout::{validate_path, Kind, CLI, DATA_FILES};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::{env, fs, path::Path};

fn main() {
    let manifest = env::var_os("CARGO_MANIFEST_DIR").unwrap();
    let manifest = Path::new(&manifest);
    let checkout = manifest.parent().unwrap().parent().unwrap();
    let stage = manifest.join("bundle-resources");
    let profile = env::var("PROFILE").unwrap();
    let cli = checkout.join(format!("target/{profile}/openkakao-cli"));
    println!("cargo:rerun-if-changed=src/resource_layout.rs");
    for relative in DATA_FILES.iter().copied().chain(std::iter::once(CLI)) {
        let source = if relative == CLI {
            cli.clone()
        } else {
            checkout.join(relative)
        };
        let kind = if relative == CLI {
            Kind::Executable
        } else {
            Kind::Data
        };
        println!("cargo:rerun-if-changed={}", source.display());
        validate_path(&source, kind).unwrap_or_else(|error| {
            panic!("unsafe/missing bundle input {}: {error:?}; build the CLI with cargo build {} --bin openkakao-cli first", source.display(), if profile == "release" { "--release" } else { "" })
        });
        let dest = stage.join(relative);
        // Create only fixed directories, validating each before descending.
        let mut parent = manifest.to_path_buf();
        for component in dest
            .parent()
            .unwrap()
            .strip_prefix(manifest)
            .unwrap()
            .components()
        {
            parent.push(component);
            match fs::create_dir(&parent) {
                Ok(()) => (),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
                Err(e) => panic!("cannot create resource directory: {e}"),
            }
            validate_path(&parent, Kind::Directory).expect("unsafe staging directory");
        }
        if fs::symlink_metadata(&dest).is_ok() {
            validate_path(&dest, kind).expect("unsafe staged resource");
        }
        // No recursive copies, imports, interpreter execution, or secret/venv reads.
        // Replace through a fresh inode so preexisting hard links are not truncated.
        let temporary = dest.with_extension(format!("stage-{}", std::process::id()));
        let mut output = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(if relative == CLI { 0o755 } else { 0o644 })
            .open(&temporary)
            .unwrap();
        let mut input = fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&source)
            .unwrap();
        let mut buffer = [0; 64 * 1024];
        loop {
            let count = input.read(&mut buffer).unwrap();
            if count == 0 {
                break;
            }
            output.write_all(&buffer[..count]).unwrap();
        }
        output
            .set_permissions(fs::Permissions::from_mode(if relative == CLI {
                0o755
            } else {
                0o644
            }))
            .unwrap();
        drop(output);
        fs::rename(&temporary, &dest).unwrap();
    }
    tauri_build::build();
}
