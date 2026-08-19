import subprocess
from langchain_core.tools import tool

@tool
def execute_terminal_command(command: str) -> str:
    """
    Executes a bash/terminal command on the Raspberry Pi and returns the output.
    Use this to start/stop apps, check system status, or manage files.
    """
    # 🛡️ SECURITY: Blocklist of highly dangerous commands
    forbidden_commands = ["rm -rf /", "mkfs", ":(){ :|:& };:"] 
    
    for forbidden in forbidden_commands:
        if forbidden in command:
            return f"Action Blocked: The command '{forbidden}' is restricted for system safety."

    try:
        # Run the command in the bash shell with a 15-second timeout
        result = subprocess.run(
            command, 
            shell=True, 
            text=True, 
            capture_output=True, 
            timeout=15
        )
        
        # Return stdout if successful, or stderr if it failed
        if result.returncode == 0:
            return f"Terminal Output:\n{result.stdout}"
        else:
            return f"Terminal Error:\n{result.stderr}"
            
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 15 seconds."
    except Exception as e:
        return f"System Error: {str(e)}"