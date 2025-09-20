"""setup_freeze.py - creates an executable from app.py"""
import sys
import os
import glob
from cx_Freeze import setup, Executable, bdist_msi
import certifi
# Find the location of zlib.dll in the Python installation
def find_zlib_dll():
    # Try to find zlib.dll in common locations
    python_dir = os.path.dirname(sys.executable)
    possible_paths = [
        os.path.join(python_dir, 'zlib.dll'),
        os.path.join(python_dir, 'DLLs', 'zlib.dll'),
        os.path.join(python_dir, 'lib', 'zlib.dll'),
    ]

    # Also search in site-packages directories
    site_packages = glob.glob(os.path.join(python_dir, 'lib', 'site-packages', '*'))
    site_packages.extend(glob.glob(os.path.join(python_dir, 'Lib', 'site-packages', '*')))

    for path in possible_paths:
        if os.path.exists(path):
            print(f"Found zlib.dll at: {path}")
            return path
        
    # Look in PATH
    for path_dir in os.environ.get('PATH', '').split(os.pathsep):
        dll_path = os.path.join(path_dir, 'zlib.dll')
        if os.path.exists(dll_path):
            print(f"Found zlib.dll at: {dll_path}")
            return dll_path
    print("Warning: zlib.dll not found in common locations")
    return None

# Get the path to zlib.dll
zlib_path = find_zlib_dll()

# Convert the PNG logo to ICO if needed
def ensure_icon_exists():
    if os.path.exists("app.ico"):
        return "app.ico"
    
    if os.path.exists("Product_Hevolve_Logo.png"):
        try:
            from PIL import Image
            
            # Create icon in multiple sizes
            img = Image.open("Product_Hevolve_Logo.png")
            icon_sizes = [(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)]
            img_list = []
            
            # Create resized versions
            for size in icon_sizes:
                resized_img = img.resize(size, Image.LANCZOS)
                img_list.append(resized_img)
            
            # Save as ICO
            img_list[0].save(
                "app.ico",
                format="ICO",
                sizes=[(img.width, img.height) for img in img_list],
                append_images=img_list[1:]
            )
            print("Successfully converted Product_Hevolve_Logo.png to app.ico")
            return "app.ico"
        except Exception as e:
            print(f"Error converting logo to ico: {str(e)}")
            return None
    
    return None

# Get icon path
icon_path = ensure_icon_exists()

# Creating the manifest file if it doesn't exists
def ensure_manifest_exists():
    manifest_content = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="HevolveAiAgentCompanion" version="1.0.0.0"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="requireAdministrator" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
</assembly>"""

    manifest_path = 'app.manifest'

    # Write the manifest file if it doesn't exists
    if not os.path.exists(manifest_path):
        with open(manifest_path, 'w') as f:
            f.write(manifest_content)
        print(f"Created manifest file at: {manifest_path}")

    return manifest_path

manifest_path = ensure_manifest_exists()
# Dependencies are automatically detected, but it might need fine-tuning.
build_exe_options = {
    "packages": [
        "os", 
        "sys", 
        "flask", 
        "threading", 
        "logging", 
        "webview", 
        "argparse",
        "importlib", 
        "traceback", 
        "json", 
        "time", 
        "ctypes",
        "pathlib",
        "shutil",
        "winreg",
        "flask_cors",
        "pyautogui",
        "PIL",
        "io",
        "uuid",
        "subprocess",
        "shlex",
        "win10toast",
        "pystray",
        "pyperclip",
        "waitress",
        "requests",
        "indicator_window", # Python file
        "tkinter"
    ],
    # Add PATH to environment variables
    "zip_includes": [],
    "build_exe": "build/HevolveAiAgentCompanion",
    "excludes": ["unittest"],
    "include_files": [
        ("main.py", "main.py"),
        ("templates", "templates"),
        ("setup.py", "setup.py"),
        ("indicator_window.py", "indicator_window.py"),
        # Add cursor.png if it exists
        ("cursor.png", "cursor.png") if os.path.exists("cursor.png") else None,
        ("app.ico", "app.ico"),
        # Add the logo for branding inside the app
        ("Product_Hevolve_Logo.png", "Product_Hevolve_Logo.png") if os.path.exists("Product_Hevolve_Logo.png") else None
    ],
    # Include Flask's static and templates
    "include_msvcr": True,
    # Ensure required DLLs are included
    "bin_includes": ["zlib.dll"],
    # Tell cx_freeze not to move these DLLs into the zip file
    "bin_path_includes": ["zlib"]
}

# If we found zlib.dll, explicitly include it
if zlib_path:
    build_exe_options["include_files"].append((zlib_path, "zlib.dll"))
    
# Remove None values from include_files
build_exe_options["include_files"] = [item for item in build_exe_options["include_files"] if item is not None]

# Add embedded Python package if it exists
if os.path.exists("python-embed"):
    print("Including embedded Python package...")
    build_exe_options["include_files"].append(("python-embed", "python-embed"))

# GUI applications require a different base on Windows
base = None
if sys.platform == "win32":
    base = "Win32GUI"

# MSI installer options
bdist_msi_options = {
    'upgrade_code': '{CE90A170-5A9A-4EB6-85A5-9A6FE3A1C587}',  # Use a unique GUID here
    'add_to_path': False,
    'initial_target_dir': r'[ProgramFilesFolder]\Hevolve AI\Agent Companion',
    # Product information
    'summary_data': {
        'author': 'Hevolve AI',
        'comments': 'Hevolve AI Agent Companion Application',
        'keywords': 'Hevolve, AI, Agent'
    },
}

# Create executable
executables = [
    Executable(
        "app.py", 
        base=base,
        target_name="HevolveAiAgentCompanion.exe",
        icon=icon_path,
        shortcut_name="Hevolve AI Agent Companion",
        shortcut_dir="ProgramMenuFolder",
        manifest = manifest_path,
        uac_admin=True # Fallback
    )
]

# Custom MSI class to add shortcuts and registry entries
class bdist_msi_custom(bdist_msi):
    def add_shortcuts(self):
        data_dir = os.path.join(self.bdist_dir, "data")
        for exe in self.distribution.executables:
            exe_name = os.path.basename(exe.target_name)
            exe_path = "[TARGETDIR]" + exe_name
            
            # Create desktop shortcut
            self.add_shortcut(
                "DesktopShortcut",
                "DesktopFolder",
                "Hevolve AI Agent Companion",
                exe_path,
                None,
                None,
                None,
                None,
                "Hevolve AI Agent Companion"
            )
            
            # Create start menu shortcut
            self.add_shortcut(
                "StartMenuShortcut",
                "ProgramMenuFolder",
                "Hevolve AI Agent Companion",
                exe_path,
                None,
                None,
                None,
                None,
                "Hevolve AI Agent Companion"
            )
        
        # Add autostart registry entry
        self.add_registry_entry(
            "AutostartEntry",
            "HKEY_CURRENT_USER",
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            "HevolveAiAgentCompanion",
            "[TARGETDIR]HevolveAiAgentCompanion.exe --background",
            False
        )
        
        bdist_msi.add_shortcuts(self)

setup(
    name="HevolveAiAgentCompanion",
    version="1.1",
    description="Hevolve AI Agent Companion Application",
    author="Hevolve AI",
    options={
        "build_exe": build_exe_options,
        "bdist_msi": bdist_msi_options
    },
    executables=executables,
    cmdclass={'bdist_msi': bdist_msi_custom}
)