"""
Freerouting integration tools for KiCad PCB design.

Provides MCP tools to use the professional-grade Freerouting auto-router
via Specctra DSN/SES file exchange.

Freerouting is a mature, advanced PCB auto-router with:
- Push & shove routing with rip-up and retry
- 45-degree routing optimization
- Multi-pass progressive optimization
- Via minimization
- Net class-aware design rules
- Real-time DRC integration

GitHub: https://github.com/freerouting/freerouting
"""

import os
import re
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from kicad_mcp.utils.file_utils import get_project_files

logger = logging.getLogger(__name__)


# ============================================================================
# Freerouting Detection and Configuration
# ============================================================================

def find_freerouting_jar() -> Optional[Path]:
    """Find the Freerouting JAR file.
    
    Searches in common locations:
    1. FREEROUTING_JAR environment variable
    2. KiCad plugin directory
    3. Common installation paths
    
    Returns:
        Path to freerouting.jar or None if not found
    """
    # 1. Check environment variable
    env_path = os.environ.get("FREEROUTING_JAR")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    
    # 2. Check KiCad plugin directories
    kicad_plugin_paths = []
    
    # Windows
    if os.name == 'nt':
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            kicad_plugin_paths.extend([
                Path(appdata) / "kicad" / "scripting" / "plugins" / "kicad-freerouting" / "plugins" / "jar",
                Path(appdata) / "kicad" / "8.0" / "scripting" / "plugins" / "kicad-freerouting" / "plugins" / "jar",
                Path(appdata) / "kicad" / "9.0" / "scripting" / "plugins" / "kicad-freerouting" / "plugins" / "jar",
            ])
        # Also check Program Files
        program_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        kicad_plugin_paths.append(Path(program_files) / "KiCad" / "share" / "kicad" / "scripting" / "plugins")
    
    # Linux
    elif os.name == 'posix':
        home = Path.home()
        kicad_plugin_paths.extend([
            home / ".local" / "share" / "kicad" / "scripting" / "plugins" / "kicad-freerouting" / "plugins" / "jar",
            home / ".local" / "share" / "kicad" / "8.0" / "scripting" / "plugins" / "kicad-freerouting" / "plugins" / "jar",
            home / ".local" / "share" / "kicad" / "9.0" / "scripting" / "plugins" / "kicad-freerouting" / "plugins" / "jar",
            Path("/usr/share/kicad/scripting/plugins"),
        ])
    
    # macOS
    if os.name == 'posix' and Path("/Applications").exists():
        home = Path.home()
        kicad_plugin_paths.extend([
            home / "Library" / "Preferences" / "kicad" / "scripting" / "plugins" / "kicad-freerouting" / "plugins" / "jar",
            Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/scripting/plugins"),
        ])
    
    # Search for JAR files
    for plugin_dir in kicad_plugin_paths:
        if plugin_dir.exists():
            # Look for freerouting*.jar
            for jar in plugin_dir.glob("freerouting*.jar"):
                if jar.is_file():
                    logger.info(f"Found Freerouting JAR: {jar}")
                    return jar
    
    # 3. Check common download locations and local MCP directory
    common_paths = [
        # Local MCP freerouting directory (bundled with this project)
        Path(__file__).parent.parent.parent / "freerouting" / "freerouting-2.1.0.jar",
        Path.home() / "Downloads" / "freerouting-2.1.0.jar",
        Path.home() / "freerouting" / "freerouting.jar",
        Path("/opt/freerouting/freerouting.jar"),
    ]
    
    for path in common_paths:
        if path.is_file():
            logger.info(f"Found Freerouting JAR: {path}")
            return path
    
    return None


def find_java() -> Optional[str]:
    """Find Java executable.
    
    Returns:
        Path to java executable or None if not found
    """
    # Check if java is in PATH
    java_path = shutil.which("java")
    if java_path:
        return java_path
    
    # Check JAVA_HOME
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        java_exe = Path(java_home) / "bin" / "java"
        if os.name == 'nt':
            java_exe = java_exe.with_suffix(".exe")
        if java_exe.is_file():
            return str(java_exe)
    
    return None


def get_java_version(java_path: str) -> Optional[str]:
    """Get Java version string.
    
    Returns:
        Version string like "21.0.1" or None if failed
    """
    try:
        result = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        # Java version is typically in stderr
        output = result.stderr or result.stdout
        match = re.search(r'version "([^"]+)"', output)
        if match:
            return match.group(1)
    except Exception as e:
        logger.warning(f"Failed to get Java version: {e}")
    return None


# ============================================================================
# DSN Export (using KiCad's Python)
# ============================================================================

def find_kicad_python() -> Optional[str]:
    """Find KiCad's bundled Python executable.
    
    Returns:
        Path to python executable or None if not found
    """
    if os.name == 'nt':
        possible_paths = [
            Path(os.environ.get("PROGRAMFILES", "")) / "KiCad" / "9.0" / "bin" / "python.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "KiCad" / "8.0" / "bin" / "python.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "KiCad" / "bin" / "python.exe",
        ]
        for p in possible_paths:
            if p.is_file():
                return str(p)
    else:
        # Linux/macOS - try system python with pcbnew
        kicad_python = shutil.which("kicad-python")
        if kicad_python:
            return kicad_python
    
    return None


def export_dsn(pcb_path: str, dsn_path: str) -> bool:
    """Export PCB to Specctra DSN format using KiCad's Python.
    
    Args:
        pcb_path: Path to .kicad_pcb file
        dsn_path: Output path for .dsn file
        
    Returns:
        True if successful
    """
    kicad_python = find_kicad_python()
    if not kicad_python:
        logger.error("KiCad Python not found")
        return False
    
    # Python script to export DSN
    script = f'''
import sys
sys.path.insert(0, r"C:\\Program Files\\KiCad\\9.0\\bin\\Lib\\site-packages")
try:
    import pcbnew
    board = pcbnew.LoadBoard(r"{pcb_path}")
    ok = pcbnew.ExportSpecctraDSN(board, r"{dsn_path}")
    sys.exit(0 if ok else 1)
except Exception as e:
    print(f"Error: {{e}}", file=sys.stderr)
    sys.exit(1)
'''
    
    try:
        result = subprocess.run(
            [kicad_python, "-c", script],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            logger.error(f"DSN export failed: {result.stderr}")
            return False
        
        return Path(dsn_path).is_file()
        
    except subprocess.TimeoutExpired:
        logger.error("DSN export timed out")
        return False
    except Exception as e:
        logger.error(f"DSN export error: {e}")
        return False


# ============================================================================
# SES Import (using KiCad's Python)
# ============================================================================

def import_ses(pcb_path: str, ses_path: str) -> bool:
    """Import Specctra SES session file into PCB using KiCad's Python.
    
    Args:
        pcb_path: Path to .kicad_pcb file
        ses_path: Path to .ses file to import
        
    Returns:
        True if successful
    """
    kicad_python = find_kicad_python()
    if not kicad_python:
        logger.error("KiCad Python not found")
        return False
    
    # Python script to import SES
    script = f'''
import sys
sys.path.insert(0, r"C:\\Program Files\\KiCad\\9.0\\bin\\Lib\\site-packages")
try:
    import pcbnew
    board = pcbnew.LoadBoard(r"{pcb_path}")
    ok = pcbnew.ImportSpecctraSES(board, r"{ses_path}")
    if ok:
        board.Save(r"{pcb_path}")
    sys.exit(0 if ok else 1)
except Exception as e:
    print(f"Error: {{e}}", file=sys.stderr)
    sys.exit(1)
'''
    
    try:
        result = subprocess.run(
            [kicad_python, "-c", script],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            logger.error(f"SES import failed: {result.stderr}")
            return False
        
        return True
        
    except subprocess.TimeoutExpired:
        logger.error("SES import timed out")
        return False
    except Exception as e:
        logger.error(f"SES import error: {e}")
        return False


# ============================================================================
# Freerouting Execution
# ============================================================================

def run_freerouting(
    dsn_path: str,
    ses_path: str,
    jar_path: str,
    java_path: str,
    skip_nets: Optional[List[str]] = None,
    timeout: int = 600,  # 10 minutes default
    max_passes: Optional[int] = None
) -> Dict[str, Any]:
    """Run Freerouting in headless CLI mode.
    
    Args:
        dsn_path: Input DSN file path
        ses_path: Output SES file path
        jar_path: Path to freerouting.jar
        java_path: Path to java executable
        skip_nets: List of net names to skip (e.g., ["GND", "VCC"])
        timeout: Maximum time in seconds
        max_passes: Maximum routing passes (None = unlimited)
        
    Returns:
        Dictionary with routing results
    """
    cmd = [
        java_path,
        "-jar", jar_path,
        "-de", dsn_path,  # Design input
        "-do", ses_path,  # Design output (session file)
        "-mp", str(max_passes) if max_passes else "100",  # Max passes
        "-host", "KiCad-MCP"
    ]
    
    # Add nets to ignore
    if skip_nets:
        cmd.extend(["-inc", ",".join(skip_nets)])
    
    logger.info(f"Running Freerouting: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        success = result.returncode == 0 and Path(ses_path).is_file()
        
        return {
            "success": success,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "ses_created": Path(ses_path).is_file()
        }
        
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Freerouting timed out after {timeout} seconds"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================================
# MCP Tool Registration
# ============================================================================

def register_freerouting_tools(mcp):
    """Register Freerouting tools with the MCP server."""
    
    @mcp.tool()
    async def freeroute_pcb(
        project_path: str,
        skip_nets: Optional[List[str]] = None,
        max_passes: int = 100,
        timeout: int = 600
    ) -> Dict[str, Any]:
        """Auto-route PCB using Freerouting professional auto-router.
        
        Freerouting is a mature, production-quality PCB auto-router that provides:
        - Push & shove routing with rip-up and retry algorithms
        - 45-degree routing for optimal trace lengths
        - Multi-pass progressive optimization
        - Via minimization
        - Net class-aware design rules
        
        This tool exports the PCB to Specctra DSN format, runs Freerouting
        in headless mode, and imports the routed result back into the PCB.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            skip_nets: List of net names to skip routing (e.g., ["GND"] - use pour instead)
            max_passes: Maximum routing passes (default 100)
            timeout: Maximum time in seconds (default 600 = 10 minutes)
            
        Returns:
            Dictionary with routing results
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        pcb_path = files["pcb"]
        
        # Find Freerouting
        jar_path = find_freerouting_jar()
        if not jar_path:
            return {
                "error": "Freerouting not found. Install via KiCad Plugin Manager (Ctrl+M) or set FREEROUTING_JAR environment variable.",
                "help": "Download from: https://github.com/freerouting/freerouting/releases"
            }
        
        # Find Java
        java_path = find_java()
        if not java_path:
            return {
                "error": "Java not found. Install Java JRE 21+ from https://adoptium.net/",
                "help": "Set JAVA_HOME environment variable or add java to PATH"
            }
        
        # Check Java version
        java_version = get_java_version(java_path)
        if java_version:
            try:
                major = int(java_version.split(".")[0])
                if major < 21:
                    return {
                        "error": f"Java version {java_version} is too old. Freerouting requires Java 21+",
                        "help": "Download from: https://adoptium.net/"
                    }
            except ValueError:
                pass  # Can't parse version, continue anyway
        
        # Default skip nets
        if skip_nets is None:
            skip_nets = ["GND"]  # Ground should use copper pour
        
        # Create temp directory for DSN/SES files
        pcb_dir = Path(pcb_path).parent
        dsn_path = str(pcb_dir / "freerouting_temp.dsn")
        ses_path = str(pcb_dir / "freerouting_temp.ses")
        
        try:
            # Step 1: Export DSN
            logger.info("Exporting PCB to DSN format...")
            if not export_dsn(pcb_path, dsn_path):
                return {"error": "Failed to export PCB to DSN format"}
            
            # Step 2: Run Freerouting
            logger.info("Running Freerouting auto-router...")
            result = run_freerouting(
                dsn_path=dsn_path,
                ses_path=ses_path,
                jar_path=str(jar_path),
                java_path=java_path,
                skip_nets=skip_nets,
                timeout=timeout,
                max_passes=max_passes
            )
            
            if not result.get("success"):
                return {
                    "error": f"Freerouting failed: {result.get('error', result.get('stderr', 'Unknown error'))}",
                    "details": result
                }
            
            # Step 3: Import SES
            logger.info("Importing routed result...")
            if not import_ses(pcb_path, ses_path):
                return {
                    "error": "Failed to import SES file into PCB",
                    "freerouting_result": result
                }
            
            return {
                "success": True,
                "message": "PCB routed successfully with Freerouting",
                "skipped_nets": skip_nets,
                "max_passes": max_passes,
                "freerouting_output": result.get("stdout", "")[:1000]  # Truncate output
            }
            
        finally:
            # Cleanup temp files
            for temp_file in [dsn_path, ses_path]:
                try:
                    if Path(temp_file).is_file():
                        Path(temp_file).unlink()
                except Exception:
                    pass
    
    @mcp.tool()
    async def check_freerouting_installation() -> Dict[str, Any]:
        """Check if Freerouting and Java are properly installed.
        
        Verifies:
        - Java JRE 21+ is installed and accessible
        - Freerouting JAR file is found
        - kicad-cli is available for DSN/SES conversion
        
        Returns:
            Dictionary with installation status and paths
        """
        result = {
            "java": {"installed": False},
            "freerouting": {"installed": False},
            "kicad_cli": {"installed": False}
        }
        
        # Check Java
        java_path = find_java()
        if java_path:
            result["java"]["installed"] = True
            result["java"]["path"] = java_path
            result["java"]["version"] = get_java_version(java_path)
        else:
            result["java"]["help"] = "Install from https://adoptium.net/"
        
        # Check Freerouting
        jar_path = find_freerouting_jar()
        if jar_path:
            result["freerouting"]["installed"] = True
            result["freerouting"]["path"] = str(jar_path)
        else:
            result["freerouting"]["help"] = "Install via KiCad Plugin Manager (Ctrl+M) or download from https://github.com/freerouting/freerouting"
        
        # Check KiCad Python (for DSN/SES conversion)
        kicad_python = find_kicad_python()
        if kicad_python:
            result["kicad_python"] = {"installed": True, "path": kicad_python}
        else:
            result["kicad_python"] = {
                "installed": False,
                "help": "KiCad Python should be installed with KiCad 8.0+"
            }
        
        # Overall status
        result["ready"] = all([
            result["java"]["installed"],
            result["freerouting"]["installed"],
            result.get("kicad_python", {}).get("installed", False)
        ])
        
        if result["ready"]:
            result["message"] = "Freerouting is ready to use!"
        else:
            missing = [k for k, v in result.items() if isinstance(v, dict) and not v.get("installed")]
            result["message"] = f"Missing components: {', '.join(missing)}"
        
        return result
    
    @mcp.tool()
    async def export_dsn_file(project_path: str) -> Dict[str, Any]:
        """Export PCB to Specctra DSN format for external routing.
        
        Creates a .dsn file that can be opened in Freerouting GUI or
        other Specctra-compatible routers.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Dictionary with export result and DSN file path
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        pcb_path = files["pcb"]
        pcb_dir = Path(pcb_path).parent
        pcb_name = Path(pcb_path).stem
        dsn_path = str(pcb_dir / f"{pcb_name}.dsn")
        
        if export_dsn(pcb_path, dsn_path):
            return {
                "success": True,
                "message": f"Exported DSN file for external routing",
                "dsn_path": dsn_path,
                "help": "Open this file in Freerouting GUI, route, then save as .ses and use import_ses_file"
            }
        else:
            return {"error": "Failed to export DSN file"}
    
    @mcp.tool()
    async def import_ses_file(project_path: str, ses_path: str) -> Dict[str, Any]:
        """Import a Specctra SES session file into the PCB.
        
        Imports routing results from Freerouting or other Specctra-compatible
        routers back into the KiCad PCB.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            ses_path: Path to the .ses file to import
            
        Returns:
            Dictionary with import result
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        pcb_path = files["pcb"]
        
        if not Path(ses_path).is_file():
            return {"error": f"SES file not found: {ses_path}"}
        
        if import_ses(pcb_path, ses_path):
            return {
                "success": True,
                "message": "Imported SES routing into PCB"
            }
        else:
            return {"error": "Failed to import SES file"}
    
    logger.info("Registered Freerouting tools: freeroute_pcb, check_freerouting_installation, export_dsn_file, import_ses_file")
