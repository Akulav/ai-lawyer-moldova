//! Embeds the icon and the version information shown in Explorer's file properties.
use std::path::PathBuf;

fn main() {
    // The app's sources are embedded with include_dir!, so a change to them must rebuild the exe.
    for p in ["../app", "../pyproject.toml", "../uv.lock", "assets/icon.ico"] {
        println!("cargo:rerun-if-changed={p}");
    }
    let version = std::env::var("CARGO_PKG_VERSION").unwrap();
    let mut parts: Vec<u16> = version.split(['.', '-']).filter_map(|p| p.parse().ok()).collect();
    parts.resize(4, 0);
    let commas = parts.iter().map(u16::to_string).collect::<Vec<_>>().join(",");
    let icon = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap()).join("assets").join("icon.ico");
    let rc = format!(
        r#"1 ICON "{icon}"
1 VERSIONINFO
FILEVERSION {commas}
PRODUCTVERSION {commas}
FILETYPE 0x1
{{
  BLOCK "StringFileInfo"
  {{
    BLOCK "041804b0"
    {{
      VALUE "CompanyName", "Avocat AI Moldova"
      VALUE "FileDescription", "Avocat AI Moldova"
      VALUE "FileVersion", "{version}"
      VALUE "ProductName", "Avocat AI Moldova"
      VALUE "ProductVersion", "{version}"
      VALUE "OriginalFilename", "AvocatAI.exe"
    }}
  }}
  BLOCK "VarFileInfo"
  {{
    VALUE "Translation", 0x0418, 1200
  }}
}}
"#,
        icon = icon.display().to_string().replace('\\', "\\\\"),
    );
    let out = PathBuf::from(std::env::var("OUT_DIR").unwrap()).join("app.rc");
    std::fs::write(&out, rc).unwrap();
    embed_resource::compile(&out, embed_resource::NONE).manifest_optional().unwrap();
}
