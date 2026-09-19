"""
Export tools for KiCad projects.
"""
import os
import tempfile
import subprocess
import shutil
import asyncio
from typing import Dict, Any, Optional
from mcp.server.fastmcp import FastMCP, Context, Image

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.config import KICAD_APP_PATH, system
from kicad_mcp.utils.kicad_cli import KiCadCLIError, get_kicad_cli_path

def register_export_tools(mcp: FastMCP) -> None:
    """Register export tools with the MCP server.
    
    Args:
        mcp: The FastMCP server instance
    """
    
    @mcp.tool()
    async def generate_pcb_thumbnail(project_path: str, ctx: Context = None):
        """Generate a thumbnail image of a KiCad PCB layout using kicad-cli.

        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            ctx: Context for MCP communication

        Returns:
            Thumbnail image of the PCB or None if generation failed
        """
        try:
            # Access the context (with null check)
            app_context = None
            if ctx:
                app_context = ctx.request_context.lifespan_context
            # Removed check for kicad_modules_available as we now use CLI
            
            print(f"Generating thumbnail via CLI for project: {project_path}")

            if not os.path.exists(project_path):
                print(f"Project not found: {project_path}")
                if ctx:
                    await ctx.info(f"Project not found: {project_path}")
                return None

            # Get PCB file from project
            files = get_project_files(project_path)
            if "pcb" not in files:
                print("PCB file not found in project")
                if ctx:
                    await ctx.info("PCB file not found in project")
                return None

            pcb_file = files["pcb"]
            print(f"Found PCB file: {pcb_file}")

            # Check cache
            cache_key = f"thumbnail_cli_{pcb_file}_{os.path.getmtime(pcb_file)}"
            if app_context and hasattr(app_context, 'cache') and cache_key in app_context.cache:
                print(f"Using cached CLI thumbnail for {pcb_file}")
                return app_context.cache[cache_key]

            if ctx:
                await ctx.report_progress(10, 100)
                await ctx.info(f"Generating thumbnail for {os.path.basename(pcb_file)} using kicad-cli")

            # Use command-line tools
            try:
                thumbnail = await generate_thumbnail_with_cli(pcb_file, ctx)
                if thumbnail:
                    # Cache the result if possible
                    if app_context and hasattr(app_context, 'cache'):
                        app_context.cache[cache_key] = thumbnail
                    print("Thumbnail generated successfully via CLI.")
                    return thumbnail
                else:
                     print("generate_thumbnail_with_cli returned None")
                     if ctx:
                         await ctx.info("Failed to generate thumbnail using kicad-cli.")
                     return None
            except Exception as e:
                print(f"Error calling generate_thumbnail_with_cli: {str(e)}", exc_info=True)
                if ctx:
                    await ctx.info(f"Error generating thumbnail with kicad-cli: {str(e)}")
                return None
            
        except asyncio.CancelledError:
            print("Thumbnail generation cancelled")
            raise  # Re-raise to let MCP know the task was cancelled
        except Exception as e:
            print(f"Unexpected error in thumbnail generation: {str(e)}")
            if ctx:
                await ctx.info(f"Error: {str(e)}")
            return None

    @mcp.tool()
    async def refill_zones(project_path: str) -> Dict[str, Any]:
        """Refill all copper zones in the PCB.
        
        This is essential after importing routed traces from Freerouting,
        as the auto-router doesn't know about copper pours.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Dictionary with refill results
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        pcb_path = files["pcb"]
        
        try:
            kicad_cli = get_kicad_cli_path()
        except KiCadCLIError as e:
            return {"error": str(e)}
        
        try:
            # Use kicad-cli to export DRC which triggers zone refill
            # Then we re-save the board
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.rpt', delete=False) as tmp:
                tmp_path = tmp.name
            
            cmd = [
                kicad_cli, "pcb", "drc",
                "--output", tmp_path,
                "--severity-all",
                pcb_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            # Clean up temp file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            if result.returncode != 0:
                return {"error": f"Zone refill failed: {result.stderr}"}

            return {
                "success": True,
                "message": "Zones refilled via DRC check",
                "pcb_path": pcb_path
            }
            
        except subprocess.TimeoutExpired:
            return {"error": "Zone refill timed out"}
        except Exception as e:
            return {"error": f"Failed to refill zones: {str(e)}"}

    @mcp.tool()
    async def export_gerbers(
        project_path: str,
        output_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """Export Gerber manufacturing files for PCB fabrication.
        
        Exports all required layers: copper, mask, silk, edge cuts.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            output_dir: Output directory (default: project_dir/gerbers)
            
        Returns:
            Dictionary with export results and file paths
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        pcb_path = files["pcb"]
        project_dir = os.path.dirname(pcb_path)
        
        if output_dir is None:
            output_dir = os.path.join(project_dir, "gerbers")
        
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            kicad_cli = get_kicad_cli_path()
        except KiCadCLIError as e:
            return {"error": str(e)}
        
        try:
            cmd = [
                kicad_cli, "pcb", "export", "gerbers",
                "--output", output_dir,
                pcb_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return {"error": f"Gerber export failed: {result.stderr}"}
            
            # List generated files
            gerber_files = [f for f in os.listdir(output_dir) if f.endswith(('.gbr', '.gtl', '.gbl', '.gts', '.gbs', '.gto', '.gbo', '.gm1'))]
            
            return {
                "success": True,
                "output_dir": output_dir,
                "files": gerber_files,
                "file_count": len(gerber_files)
            }
            
        except subprocess.TimeoutExpired:
            return {"error": "Gerber export timed out"}
        except Exception as e:
            return {"error": f"Failed to export Gerbers: {str(e)}"}

    @mcp.tool()
    async def export_drill(
        project_path: str,
        output_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """Export drill files for PCB fabrication.
        
        Exports Excellon drill files (.drl) for through-holes and vias.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            output_dir: Output directory (default: project_dir/gerbers)
            
        Returns:
            Dictionary with export results and file paths
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        pcb_path = files["pcb"]
        project_dir = os.path.dirname(pcb_path)
        
        if output_dir is None:
            output_dir = os.path.join(project_dir, "gerbers")
        
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            kicad_cli = get_kicad_cli_path()
        except KiCadCLIError as e:
            return {"error": str(e)}
        
        try:
            cmd = [
                kicad_cli, "pcb", "export", "drill",
                "--output", output_dir,
                pcb_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return {"error": f"Drill export failed: {result.stderr}"}
            
            # List generated files
            drill_files = [f for f in os.listdir(output_dir) if f.endswith(('.drl', '.xln'))]
            
            return {
                "success": True,
                "output_dir": output_dir,
                "files": drill_files,
                "file_count": len(drill_files)
            }
            
        except subprocess.TimeoutExpired:
            return {"error": "Drill export timed out"}
        except Exception as e:
            return {"error": f"Failed to export drill files: {str(e)}"}

    @mcp.tool()
    async def export_pos(
        project_path: str,
        output_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """Export pick and place file for PCB assembly.
        
        Exports component positions for automated assembly machines.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            output_dir: Output directory (default: project_dir/assembly)
            
        Returns:
            Dictionary with export results and file paths
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        pcb_path = files["pcb"]
        project_dir = os.path.dirname(pcb_path)
        
        if output_dir is None:
            output_dir = os.path.join(project_dir, "assembly")
        
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            kicad_cli = get_kicad_cli_path()
        except KiCadCLIError as e:
            return {"error": str(e)}
        
        try:
            cmd = [
                kicad_cli, "pcb", "export", "pos",
                "--output", os.path.join(output_dir, "pos.csv"),
                "--format", "csv",
                "--units", "mm",
                pcb_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return {"error": f"PnP export failed: {result.stderr}"}
            
            # List generated files
            pos_files = [f for f in os.listdir(output_dir) if f.endswith(('.csv', '.pos'))]
            
            return {
                "success": True,
                "output_dir": output_dir,
                "files": pos_files,
                "file_count": len(pos_files)
            }
            
        except subprocess.TimeoutExpired:
            return {"error": "PnP export timed out"}
        except Exception as e:
            return {"error": f"Failed to export PnP file: {str(e)}"}

    @mcp.tool()
    async def export_step(
        project_path: str,
        output_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """Export the board as a STEP model for mechanical CAD.

        Needs the 3D models wired to the footprints, and a board thickness
        set in the stackup - a deck with the default thickness exports a
        board of the wrong height, which is exactly what an enclosure check
        is meant to catch.

        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            output_file: Output .step path (default: alongside the PCB)

        Returns:
            Dictionary with export results and the file path
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}

        pcb_path = files["pcb"]
        if output_file is None:
            base = os.path.splitext(pcb_path)[0]
            output_file = f"{base}.step"

        try:
            kicad_cli = get_kicad_cli_path()
        except KiCadCLIError as e:
            return {"error": str(e)}

        try:
            cmd = [
                kicad_cli, "pcb", "export", "step",
                "--output", output_file,
                "--subst-models",
                pcb_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                return {"error": f"STEP export failed: {result.stderr}"}
            if not os.path.exists(output_file):
                return {"error": "STEP export reported success but wrote no file"}

            return {
                "success": True,
                "output_file": output_file,
                "size_bytes": os.path.getsize(output_file)
            }

        except subprocess.TimeoutExpired:
            return {"error": "STEP export timed out"}
        except Exception as e:
            return {"error": f"Failed to export STEP: {str(e)}"}

    @mcp.tool()
    async def export_spice_netlist(
        project_path: str,
        output_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """Export the schematic as a SPICE netlist for simulation.

        Connectivity and values come out mechanically; active parts still
        need their own model cards, so a passive or linear sheet exports
        ready to simulate and anything with a diode in it does not.

        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            output_file: Output .cir path (default: alongside the schematic)

        Returns:
            Dictionary with export results and the file path
        """
        files = get_project_files(project_path)
        if "schematic" not in files:
            return {"error": "Schematic file not found in project"}

        sch_path = files["schematic"]
        if output_file is None:
            base = os.path.splitext(sch_path)[0]
            output_file = f"{base}.cir"

        try:
            kicad_cli = get_kicad_cli_path()
        except KiCadCLIError as e:
            return {"error": str(e)}

        try:
            cmd = [
                kicad_cli, "sch", "export", "netlist",
                "--format", "spice",
                "--output", output_file,
                sch_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.returncode != 0:
                return {"error": f"SPICE netlist export failed: {result.stderr}"}
            if not os.path.exists(output_file):
                return {"error": "Netlist export reported success but wrote no file"}

            return {
                "success": True,
                "output_file": output_file,
                "size_bytes": os.path.getsize(output_file)
            }

        except subprocess.TimeoutExpired:
            return {"error": "SPICE netlist export timed out"}
        except Exception as e:
            return {"error": f"Failed to export SPICE netlist: {str(e)}"}

    # generate_project_thumbnail removed - was just an alias for generate_pcb_thumbnail

# Helper functions for thumbnail generation
async def generate_thumbnail_with_cli(pcb_file: str, ctx: Context = None):
    """Generate PCB thumbnail using command line tools.
    This is a fallback method when the kicad Python module is not available or fails.

    Args:
        pcb_file: Path to the PCB file (.kicad_pcb)
        ctx: MCP context for progress reporting

    Returns:
        Image object containing the PCB thumbnail or None if generation failed
    """
    try:
        print("Attempting to generate thumbnail using KiCad CLI tools")
        if ctx:
            await ctx.report_progress(20, 100)

        # --- Determine Output Path --- 
        project_dir = os.path.dirname(pcb_file)
        project_name = os.path.splitext(os.path.basename(pcb_file))[0]
        output_file = os.path.join(project_dir, f"{project_name}_thumbnail.svg")
        # --------------------------- 

        # Check for required command-line tools based on OS
        try:
            kicad_cli = get_kicad_cli_path()
        except KiCadCLIError as e:
            print(str(e))
            return None

        if ctx:
            await ctx.report_progress(30, 100)
            await ctx.info("Using KiCad command line tools for thumbnail generation")        # Build command for generating SVG from PCB using kicad-cli (changed from PNG)
        cmd = [
            kicad_cli,
            "pcb",
            "export",
            "svg", # <-- Changed format to svg
            "--output", output_file,
            "--layers", "F.Cu,B.Cu,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts",  # Keep relevant layers
            # Consider adding options like --black-and-white if needed
            pcb_file
        ]

        print(f"Running command: {' '.join(cmd)}")
        if ctx:
            await ctx.report_progress(50, 100)

        # Run the command
        try:
            process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            print(f"Command successful: {process.stdout}")

            if ctx:
                await ctx.report_progress(70, 100)

            # Check if the output file was created
            if not os.path.exists(output_file):
                print(f"Output file not created: {output_file}")
                return None

            # Read the image file
            with open(output_file, 'rb') as f:
                img_data = f.read()

            print(f"Successfully generated thumbnail with CLI, size: {len(img_data)} bytes")
            if ctx:
                await ctx.report_progress(90, 100)
                # Inform user about the saved file
                await ctx.info(f"Thumbnail saved to: {output_file}")
            return Image(data=img_data, format="svg") # <-- Changed format to svg

        except subprocess.CalledProcessError as e:
            print(f"Command '{' '.join(e.cmd)}' failed with code {e.returncode}")
            print(f"Stderr: {e.stderr}")
            print(f"Stdout: {e.stdout}")
            if ctx:
                await ctx.info(f"KiCad CLI command failed: {e.stderr or e.stdout}")
            return None
        except subprocess.TimeoutExpired:
            print(f"Command timed out after 30 seconds: {' '.join(cmd)}")
            if ctx:
                await ctx.info("KiCad CLI command timed out")
            return None
        except Exception as e:
            print(f"Error running CLI command: {str(e)}", exc_info=True)
            if ctx:
                await ctx.info(f"Error running KiCad CLI: {str(e)}")
            return None
                
    except asyncio.CancelledError:
        print("CLI thumbnail generation cancelled")
        raise
    except Exception as e:
        print(f"Unexpected error in CLI thumbnail generation: {str(e)}")
        if ctx:
            await ctx.info(f"Unexpected error: {str(e)}")
        return None
