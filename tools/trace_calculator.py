from langchain_core.tools import tool

@tool
def calculate_ipc2221_trace_width(current_amps: float, temp_rise_c: float, thickness_oz: float, layer_type: str = "external") -> str:
    """
    Calculates the required PCB trace width in mils based on the IPC-2221 standard.
    
    Args:
        current_amps: The maximum current in Amperes.
        temp_rise_c: The maximum allowable temperature rise in Celsius.
        thickness_oz: The copper weight in oz/ft^2 (typically 1.0 or 2.0).
        layer_type: Either 'internal' or 'external'.
    """
    # IPC-2221 curve-fitting constants
    if layer_type.lower() == "internal":
        k = 0.024
        b = 0.44
        c = 0.725
    else: # external
        k = 0.048
        b = 0.44
        c = 0.725
        
    try:
        # 1. Calculate required cross-sectional area (sq mils)
        area_mils2 = (current_amps / (k * (temp_rise_c ** b))) ** (1 / c)
        
        # 2. Calculate required width (mils)
        width_mils = area_mils2 / (thickness_oz * 1.378)
        width_mm = width_mils * 0.0254 # Convert mils to mm for metric users
        
        return (f"For {current_amps}A with a {temp_rise_c}°C rise on {thickness_oz}oz {layer_type} copper: "
                f"Required Trace Width is {width_mils:.2f} mils ({width_mm:.3f} mm).")
    except Exception as e:
        return f"Error calculating trace width: {str(e)}"